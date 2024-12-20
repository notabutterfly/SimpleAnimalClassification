from __future__ import annotations

import logging
import os
import pytz
import io
from apscheduler.schedulers.asyncio import AsyncIOScheduler
import asyncio
from datetime import time as tim
import scheduler

from uuid import uuid4
from telegram import BotCommandScopeAllGroupChats, Update, constants, PreCheckoutQuery
from telegram import InlineKeyboardMarkup, InlineKeyboardButton, InlineQueryResultArticle
from telegram import InputTextMessageContent, BotCommand, LabeledPrice
from telegram.error import RetryAfter, TimedOut, BadRequest
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, \
    filters, InlineQueryHandler, CallbackQueryHandler, Application, ContextTypes, CallbackContext, PreCheckoutQueryHandler, JobQueue


from pydub import AudioSegment
from PIL import Image

from utils import is_group_chat, get_thread_id, message_text, wrap_with_indicator, split_into_chunks, \
    edit_message_with_retry, get_stream_cutoff_values, is_allowed, get_remaining_budget, is_admin, is_within_budget, \
    get_reply_to_message_id, add_chat_request_to_usage_tracker, error_handler, is_direct_result, handle_direct_result, \
    cleanup_intermediate_files
from openai_helper import OpenAIHelper, localized_text
from usage_tracker import UsageTracker
from database import *

class ChatGPTTelegramBot:
    """
    Class representing a ChatGPT Telegram Bot.
    """

    def __init__(self, config: dict, openai: OpenAIHelper):
        """
        Initializes the bot with the given configuration and GPT bot object.
        :param config: A dictionary containing the bot configuration
        :param openai: OpenAIHelper object
        """
        self.config = config
        self.openai = openai
        bot_language = self.config['bot_language']
        self.commands = [
            BotCommand(command='help', description="Все команды"),
            BotCommand(command='reset', description="Обновить диалог"),
            BotCommand(command='resend', description="Повторить предыдущее сообщение"),
            BotCommand(command='menu', description="Меню для управления ботом"),
            BotCommand(command='privacy', description="Общая информация и пользовательское соглашение"),
            BotCommand(command='buy', description="Информация о ценах"),
            BotCommand(command='start', description="Начальная информация")
        ]
        # If imaging is enabled, add the "image" command to the list
        if self.config.get('enable_image_generation', False):
            self.commands.append(BotCommand(command='image', description=localized_text('image_description', bot_language)))

        if self.config.get('enable_tts_generation', False):
            self.commands.append(BotCommand(command='tts', description=localized_text('tts_description', bot_language)))

        self.group_commands = [BotCommand(
            command='chat', description=localized_text('chat_description', bot_language)
        )] + self.commands
        self.disallowed_message = localized_text('disallowed', bot_language)
        self.budget_limit_message = localized_text('budget_limit', bot_language)
        self.usage = {}
        self.last_message = {}
        self.inline_queries_cache = {}

    async def buy(self, update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        """
        Отправляет информацию о подписках и кнопки для покупки дополнительных запросов.
        """
        help_text = ("""SnappyGPT открывает для вас доступ к AI моделям мира в Telegram 

Здесь вы можете приобрести доступ к расширенному сервису

SnappyGPT Free | ЕЖЕДНЕВНО
☑️ 10 текстовых запросов
☑️ GPT-3.5 turbo 

Подписка SnappyGPT Middle | НА МЕСЯЦ
⁃ ✅ 100 запросов ежедневно
⁃ ✅ GPT-4o mini и Gemini Flash 1.5
⁃ ✅ голосовые вопросы и ответы
⁃ ✅ интерактивные уведомления(скоро)
⁃ ✅ работа с изображениями
⁃ Стоимость: 100⭐️ (194 р.)* 

* цены указаны в ⭐️ Stars – это валюта Telegram для оплаты ботов и приложений. 
⁃ Как купить ⭐️ Stars? 

💬 По вопросам оплаты: @snappyai_admin"""
                     )


        # Создаем кнопки для покупки дополнительных запросов
        # Отправляем текстовое сообщение с информацией о подписках
        await update.message.reply_text(help_text, disable_web_page_preview=True)

        # Создаем кнопки для покупки дополнительных запросов
        keyboard = [
            [
                InlineKeyboardButton("50 запросов: 100⭐️", callback_data='buy_50'),
                InlineKeyboardButton("100 запросов: 200⭐️", callback_data='buy_100'),
            ],
            [
                InlineKeyboardButton("200 запросов: 350⭐️", callback_data='buy_200'),
                InlineKeyboardButton("600 запросов: 1000⭐️", callback_data='buy_600')
            ],
            [
                InlineKeyboardButton("Подписка SnappyGPT Middle", callback_data='subscribe_middle')
            ]
        ]

        reply_markup = InlineKeyboardMarkup(keyboard)

        # Отправляем сообщение с кнопками
        await update.message.reply_text("""Выберите количество запросов для покупки или подписку:
Платные запросы используются после израсходования лимита бесплатных""", reply_markup=reply_markup)

    async def button_handler(self, update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        await query.answer()  # Подтверждаем нажатие кнопки
        if query.data.startswith('buy_'):
            # Получаем количество запросов из callback_data
            amount = query.data.split('_')[1]
            prices = {
                '50': 100,
                '100': 200,
                '200': 350,
                '600': 1000
            }
            price = prices[amount]
            title = f"Покупка {amount} запросов"

            # Создаем инвойс
            await query.message.reply_invoice(
                title=title,
                description=f"Вы собираетесь купить {amount} запросов за {price}⭐️",
                payload=f"buy_{amount}",
                provider_token='',  # Замените на ваш токен провайдера
                currency='XTR',
                prices=[LabeledPrice(label="XTR", amount=price)],  # Указываем цену в копейках
                start_parameter='buy_requests'
            )

        elif query.data == 'subscribe_middle':
            price = 100  # Цена подписки
            title = "Подписка SnappyGPT Middle"

            # Создаем инвойс для подписки
            await query.message.reply_invoice(
                title=title,
                description="Подписка SnappyGPT Middle на месяц за 100⭐️",
                payload="subscribe_middle",
                provider_token='',  # Замените на ваш токен провайдера
                currency='XTR',
                prices=[LabeledPrice(label="XTR", amount=price)],
                # Указываем цену в копейках
                start_parameter='subscribe_middle'
            )

    async def successful_payment_s(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        # Получаем информацию о том, что было куплено
        payment_info = update.message.successful_payment
        user_id = update.message.from_user.id
        # Определите payload
        payload = payment_info.invoice_payload

        if payload.startswith('buy_'):
            amount = payload.split('_')[1]
            # Логика для обработки покупки запросов
            # Сохраните информацию в базе данных о том, что пользователь купил запросы
            update_db(user_id, paid_requests=int(amount), prem_days=0)

        elif payload == 'subscribe_middle':
            # Логика для обработки подписки
            # Сохраните информацию о подписке в базе данных
            update_db(user_id, 0, 30)


        await update.message.reply_text("Спасибо за покупку, общую статистику можете посмотреть в /menu")

    async def pre_checkout_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE, *args) -> None:
        query = update.pre_checkout_query
        await query.answer(ok=True)  # Вызываем answer на предзаказ


    async def menu_s(self, update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        """
        Shows the menu.
        """
        user_id = update.message.from_user.id
        add_newuser_db(user_id)

        info = get_user_info_db(user_id)
        #markup = InlineKeyboardMarkup([[InlineKeyboardButton("Цены", callback_data="buy")]])
        help_text = (f"""У вас подписка: SnappyGPT Free ✔️
Выбрана модель: GPT-3.5 turbo

Бесплатных запросов на сегодня: {info[2]}

Куплено запросов: {info[3]}

Осталось дней подписки: {info[4]}

☑️ Подписка SnappyGPT Middle:
 ⁃ 100 запросов ежедневно
 ⁃ Голосовые вопросы и ответы
 ⁃ Интерактивные уведомления(в будущем)


Хочешь больше?
Подключите в разделе /buy"""
        )
        await update.message.reply_text(help_text)

    async def start_s(self, update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        user_id = update.message.from_user.id
        add_newuser_db(user_id)
        """
        Shows the help menu.
        """
        help_text = (
                """Привет, меня зовут Snappy и я твой виртуальный GPT помощник на каждый день

В будущем я смогу работать на моделях: GPT-4o, GPT-4o mini, OpenAI o1, OpenAI o1 mini,
Perplexity, Gemini 1.5 Flash, Grok AI, ЯндексGPT.

Бесплатно: GPT-3.5 turbo.

Я имею:
1. Работать с текстом
2. Работать с документами
3. Формировать задачи и пути достижения цели
4. Писать и редактировать код
5. Решать задачи по математике, физики 
6. Создавать креативные идеи
7. Голосовой ввод


Скоро: 
 ⁃ Интерактивные уведомления c интересным фактом / рецептом / цитатой или мотивацией
 ⁃ Интерактивные уведомления c вопросами по темам 
 ⁃ Интерактивные уведомления для обучение английского языка 
 ⁃ Создание изображений по запросу

Наши контакты:
 ⁃ @snappyai_tech -  официальный канал SnappyAI 
 ⁃ @snappyai_admin - контакт для связи"""
            )
        await update.message.reply_text(help_text, disable_web_page_preview=True)

    async def faq(self, update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        """
        Shows the F.A.Q.
        """
        help_text = ("""﻿﻿- Указание ценовой политики на предлагаемые услуги в валюте РФ;

 • Бесплатные запросы: 10 запросов/день на каждого пользователя.
 • Подписка: 190 руб./месяц за 100 запросов/день.
 • Покупка дополнительных запросов:
 • 50 запросов: 190 руб.
 • 100 запросов: 389 руб.
 • 200 запросов: 779 руб.
 • 600 запросов: 2337 руб.


﻿﻿- Краткое описание Вашей организации, с указанием ИНН или ОГРНИП;

ИП Толстых Никита Александрович
ИНН 744815548295 ОГРНИП: 323784700041704

﻿﻿- Контактные данные (телефон, e-mail);

+7 916 647 16 10
snappyaitech@gmail.com

﻿﻿- Договор оферты/пользовательское соглашение с клиентом (Ваш потребитель должен иметь возможность ознакомиться с условиями реализуемого Вами товара/оказываемой услуги и условиями возврата денежных средств до момента старта оплаты)

https://teletype.in/@snappyai_tech/CrvK5Rhk32x"""
        )
        await update.message.reply_text(help_text, disable_web_page_preview=True)

    async def help(self, update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        """
        Shows the help menu.
        """
        commands = self.group_commands if is_group_chat(update) else self.commands
        commands_description = [f'/{command.command} - {command.description}' for command in commands]
        bot_language = self.config['bot_language']
        help_text = (
                """📝 Генерация текстов
Для генерации текста напишите запрос в чат. Пользователи с подпиской /premium могут отправлять голосовые сообщения.

/reset - обновить диалог
/start – описание бота
/menu – ваш профиль и баланс
/buy  – покупка подписки и запросов
/privacy – пользовательское соглашение и политика конфиденциальности

По всем вопросам также можно написать администратору @snappyai_admin"""
        )
        await update.message.reply_text(help_text, disable_web_page_preview=True)

    async def resend(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """
        Resend the last request
        """
        if not await is_allowed(self.config, update, context):
            logging.warning(f'User {update.message.from_user.name}  (id: {update.message.from_user.id})'
                            ' is not allowed to resend the message')
            await self.send_disallowed_message(update, context)
            return

        chat_id = update.effective_chat.id
        if chat_id not in self.last_message:
            logging.warning(f'User {update.message.from_user.name} (id: {update.message.from_user.id})'
                            ' does not have anything to resend')
            await update.effective_message.reply_text(
                message_thread_id=get_thread_id(update),
                text=localized_text('resend_failed', self.config['bot_language'])
            )
            return

        # Update message text, clear self.last_message and send the request to prompt
        logging.info(f'Resending the last prompt from user: {update.message.from_user.name} '
                     f'(id: {update.message.from_user.id})')
        with update.message._unfrozen() as message:
            message.text = self.last_message.pop(chat_id)

        await self.prompt(update=update, context=context)

    async def reset(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """
        Resets the conversation.
        """
        if not await is_allowed(self.config, update, context):
            logging.warning(f'User {update.message.from_user.name} (id: {update.message.from_user.id}) '
                            'is not allowed to reset the conversation')
            await self.send_disallowed_message(update, context)
            return

        logging.info(f'Resetting the conversation for user {update.message.from_user.name} '
                     f'(id: {update.message.from_user.id})...')

        chat_id = update.effective_chat.id
        reset_content = message_text(update.message)
        self.openai.reset_chat_history(chat_id=chat_id, content=reset_content)
        await update.effective_message.reply_text(
            message_thread_id=get_thread_id(update),
            text=localized_text('reset_done', self.config['bot_language'])
        )

    async def image(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """
        Generates an image for the given prompt using DALL·E APIs
        """
        user_id = update.message.from_user.id
        add_newuser_db(user_id)

        info = get_user_info_db(user_id)
        if info[4] <= 0:
            await update.message.reply_text("""Генерация изображений доступна только по подписке
            Подробнее в /buy""", disable_web_page_preview=True)
            return
        if not self.config['enable_image_generation'] \
                or not await self.check_allowed_and_within_budget(update, context):
            return

        image_query = message_text(update.message)
        if image_query == '':
            await update.effective_message.reply_text(
                message_thread_id=get_thread_id(update),
                text=localized_text('image_no_prompt', self.config['bot_language'])
            )
            return

        logging.info(f'New image generation request received from user {update.message.from_user.name} '
                     f'(id: {update.message.from_user.id})')

        async def _generate():
            try:
                image_url, image_size = await self.openai.generate_image(prompt=image_query)
                if self.config['image_receive_mode'] == 'photo':
                    await update.effective_message.reply_photo(
                        reply_to_message_id=get_reply_to_message_id(self.config, update),
                        photo=image_url
                    )
                elif self.config['image_receive_mode'] == 'document':
                    await update.effective_message.reply_document(
                        reply_to_message_id=get_reply_to_message_id(self.config, update),
                        document=image_url
                    )
                else:
                    raise Exception(f"env variable IMAGE_RECEIVE_MODE has invalid value {self.config['image_receive_mode']}")
                # add image request to users usage tracker
                user_id = update.message.from_user.id
                self.usage[user_id].add_image_request(image_size, self.config['image_prices'])
                # add guest chat request to guest usage tracker
                if str(user_id) not in self.config['allowed_user_ids'].split(',') and 'guests' in self.usage:
                    self.usage["guests"].add_image_request(image_size, self.config['image_prices'])

            except Exception as e:
                logging.exception(e)
                await update.effective_message.reply_text(
                    message_thread_id=get_thread_id(update),
                    reply_to_message_id=get_reply_to_message_id(self.config, update),
                    text=f"{localized_text('image_fail', self.config['bot_language'])}: {str(e)}",
                    parse_mode=constants.ParseMode.MARKDOWN
                )

        await wrap_with_indicator(update, context, _generate, constants.ChatAction.UPLOAD_PHOTO)

    async def tts(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """
        Generates an speech for the given input using TTS APIs
        """
        user_id = update.message.from_user.id
        add_newuser_db(user_id)

        info = get_user_info_db(user_id)
        if info[4] <= 0:
            await update.message.reply_text("""Генерация голоса доступна только по подписке
                    Подробнее в /buy""", disable_web_page_preview=True)
            return
        if not self.config['enable_tts_generation'] \
                or not await self.check_allowed_and_within_budget(update, context):
            return

        tts_query = message_text(update.message)
        if tts_query == '':
            await update.effective_message.reply_text(
                message_thread_id=get_thread_id(update),
                text=localized_text('tts_no_prompt', self.config['bot_language'])
            )
            return

        logging.info(f'New speech generation request received from user {update.message.from_user.name} '
                     f'(id: {update.message.from_user.id})')

        async def _generate():
            try:
                speech_file, text_length = await self.openai.generate_speech(text=tts_query)

                await update.effective_message.reply_voice(
                    reply_to_message_id=get_reply_to_message_id(self.config, update),
                    voice=speech_file
                )
                speech_file.close()
                # add image request to users usage tracker
                user_id = update.message.from_user.id
                self.usage[user_id].add_tts_request(text_length, self.config['tts_model'], self.config['tts_prices'])
                # add guest chat request to guest usage tracker
                if str(user_id) not in self.config['allowed_user_ids'].split(',') and 'guests' in self.usage:
                    self.usage["guests"].add_tts_request(text_length, self.config['tts_model'], self.config['tts_prices'])

            except Exception as e:
                logging.exception(e)
                await update.effective_message.reply_text(
                    message_thread_id=get_thread_id(update),
                    reply_to_message_id=get_reply_to_message_id(self.config, update),
                    text=f"{localized_text('tts_fail', self.config['bot_language'])}: {str(e)}",
                    parse_mode=constants.ParseMode.MARKDOWN
                )

        await wrap_with_indicator(update, context, _generate, constants.ChatAction.UPLOAD_VOICE)

    async def transcribe(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """
        Transcribe audio messages.
        """
        if not self.config['enable_transcription'] or not await self.check_allowed_and_within_budget(update, context):
            return

        if is_group_chat(update) and self.config['ignore_group_transcriptions']:
            logging.info('Transcription coming from group chat, ignoring...')
            return

        chat_id = update.effective_chat.id
        filename = update.message.effective_attachment.file_unique_id

        async def _execute():
            filename_mp3 = f'{filename}.mp3'
            bot_language = self.config['bot_language']
            try:
                media_file = await context.bot.get_file(update.message.effective_attachment.file_id)
                await media_file.download_to_drive(filename)
            except Exception as e:
                logging.exception(e)
                await update.effective_message.reply_text(
                    message_thread_id=get_thread_id(update),
                    reply_to_message_id=get_reply_to_message_id(self.config, update),
                    text=(
                        f"{localized_text('media_download_fail', bot_language)[0]}: "
                        f"{str(e)}. {localized_text('media_download_fail', bot_language)[1]}"
                    ),
                    parse_mode=constants.ParseMode.MARKDOWN
                )
                return

            try:
                audio_track = AudioSegment.from_file(filename)
                audio_track.export(filename_mp3, format="mp3")
                logging.info(f'New transcribe request received from user {update.message.from_user.name} '
                             f'(id: {update.message.from_user.id})')

            except Exception as e:
                logging.exception(e)
                await update.effective_message.reply_text(
                    message_thread_id=get_thread_id(update),
                    reply_to_message_id=get_reply_to_message_id(self.config, update),
                    text=localized_text('media_type_fail', bot_language)
                )
                if os.path.exists(filename):
                    os.remove(filename)
                return

            user_id = update.message.from_user.id
            if user_id not in self.usage:
                self.usage[user_id] = UsageTracker(user_id, update.message.from_user.name)

            try:
                transcript = await self.openai.transcribe(filename_mp3)

                transcription_price = self.config['transcription_price']
                self.usage[user_id].add_transcription_seconds(audio_track.duration_seconds, transcription_price)

                allowed_user_ids = self.config['allowed_user_ids'].split(',')
                if str(user_id) not in allowed_user_ids and 'guests' in self.usage:
                    self.usage["guests"].add_transcription_seconds(audio_track.duration_seconds, transcription_price)

                # check if transcript starts with any of the prefixes
                response_to_transcription = any(transcript.lower().startswith(prefix.lower()) if prefix else False
                                                for prefix in self.config['voice_reply_prompts'])

                if self.config['voice_reply_transcript'] and not response_to_transcription:

                    # Split into chunks of 4096 characters (Telegram's message limit)
                    transcript_output = f"_{localized_text('transcript', bot_language)}:_\n\"{transcript}\""
                    chunks = split_into_chunks(transcript_output)

                    for index, transcript_chunk in enumerate(chunks):
                        await update.effective_message.reply_text(
                            message_thread_id=get_thread_id(update),
                            reply_to_message_id=get_reply_to_message_id(self.config, update) if index == 0 else None,
                            text=transcript_chunk,
                            parse_mode=constants.ParseMode.MARKDOWN
                        )
                else:
                    # Get the response of the transcript
                    response, total_tokens = await self.openai.get_chat_response(chat_id=chat_id, query=transcript)

                    self.usage[user_id].add_chat_tokens(total_tokens, self.config['token_price'])
                    if str(user_id) not in allowed_user_ids and 'guests' in self.usage:
                        self.usage["guests"].add_chat_tokens(total_tokens, self.config['token_price'])

                    # Split into chunks of 4096 characters (Telegram's message limit)
                    transcript_output = (
                        f"_{localized_text('transcript', bot_language)}:_\n\"{transcript}\"\n\n"
                        f"_{localized_text('answer', bot_language)}:_\n{response}"
                    )
                    chunks = split_into_chunks(transcript_output)

                    for index, transcript_chunk in enumerate(chunks):
                        await update.effective_message.reply_text(
                            message_thread_id=get_thread_id(update),
                            reply_to_message_id=get_reply_to_message_id(self.config, update) if index == 0 else None,
                            text=transcript_chunk,
                            parse_mode=constants.ParseMode.MARKDOWN
                        )

            except Exception as e:
                logging.exception(e)
                await update.effective_message.reply_text(
                    message_thread_id=get_thread_id(update),
                    reply_to_message_id=get_reply_to_message_id(self.config, update),
                    text=f"{localized_text('transcribe_fail', bot_language)}: {str(e)}",
                    parse_mode=constants.ParseMode.MARKDOWN
                )
            finally:
                if os.path.exists(filename_mp3):
                    os.remove(filename_mp3)
                if os.path.exists(filename):
                    os.remove(filename)

        await wrap_with_indicator(update, context, _execute, constants.ChatAction.TYPING)

    async def vision(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """
        Interpret image using vision model.
        """
        user_id = update.message.from_user.id
        add_newuser_db(user_id)

        info = get_user_info_db(user_id)
        if info[4] <= 0:
            await update.message.reply_text("""Анализ изображений доступен только по подписке
                    Подробнее в /buy""", disable_web_page_preview=True)
            return
        if not self.config['enable_vision'] or not await self.check_allowed_and_within_budget(update, context):
            return

        chat_id = update.effective_chat.id
        prompt = update.message.caption

        if is_group_chat(update):
            if self.config['ignore_group_vision']:
                logging.info('Vision coming from group chat, ignoring...')
                return
            else:
                trigger_keyword = self.config['group_trigger_keyword']
                if (prompt is None and trigger_keyword != '') or \
                   (prompt is not None and not prompt.lower().startswith(trigger_keyword.lower())):
                    logging.info('Vision coming from group chat with wrong keyword, ignoring...')
                    return
        
        image = update.message.effective_attachment[-1]
        

        async def _execute():
            bot_language = self.config['bot_language']
            try:
                media_file = await context.bot.get_file(image.file_id)
                temp_file = io.BytesIO(await media_file.download_as_bytearray())
            except Exception as e:
                logging.exception(e)
                await update.effective_message.reply_text(
                    message_thread_id=get_thread_id(update),
                    reply_to_message_id=get_reply_to_message_id(self.config, update),
                    text=(
                        f"{localized_text('media_download_fail', bot_language)[0]}: "
                        f"{str(e)}. {localized_text('media_download_fail', bot_language)[1]}"
                    ),
                    parse_mode=constants.ParseMode.MARKDOWN
                )
                return
            
            # convert jpg from telegram to png as understood by openai

            temp_file_png = io.BytesIO()

            try:
                original_image = Image.open(temp_file)
                
                original_image.save(temp_file_png, format='PNG')
                logging.info(f'New vision request received from user {update.message.from_user.name} '
                             f'(id: {update.message.from_user.id})')

            except Exception as e:
                logging.exception(e)
                await update.effective_message.reply_text(
                    message_thread_id=get_thread_id(update),
                    reply_to_message_id=get_reply_to_message_id(self.config, update),
                    text=localized_text('media_type_fail', bot_language)
                )
            
            

            user_id = update.message.from_user.id
            if user_id not in self.usage:
                self.usage[user_id] = UsageTracker(user_id, update.message.from_user.name)

            if self.config['stream']:

                stream_response = self.openai.interpret_image_stream(chat_id=chat_id, fileobj=temp_file_png, prompt=prompt)
                i = 0
                prev = ''
                sent_message = None
                backoff = 0
                stream_chunk = 0

                async for content, tokens in stream_response:
                    if is_direct_result(content):
                        return await handle_direct_result(self.config, update, content)

                    if len(content.strip()) == 0:
                        continue

                    stream_chunks = split_into_chunks(content)
                    if len(stream_chunks) > 1:
                        content = stream_chunks[-1]
                        if stream_chunk != len(stream_chunks) - 1:
                            stream_chunk += 1
                            try:
                                await edit_message_with_retry(context, chat_id, str(sent_message.message_id),
                                                              stream_chunks[-2])
                            except:
                                pass
                            try:
                                sent_message = await update.effective_message.reply_text(
                                    message_thread_id=get_thread_id(update),
                                    text=content if len(content) > 0 else "..."
                                )
                            except:
                                pass
                            continue

                    cutoff = get_stream_cutoff_values(update, content)
                    cutoff += backoff

                    if i == 0:
                        try:
                            if sent_message is not None:
                                await context.bot.delete_message(chat_id=sent_message.chat_id,
                                                                 message_id=sent_message.message_id)
                            sent_message = await update.effective_message.reply_text(
                                message_thread_id=get_thread_id(update),
                                reply_to_message_id=get_reply_to_message_id(self.config, update),
                                text=content,
                            )
                        except:
                            continue

                    elif abs(len(content) - len(prev)) > cutoff or tokens != 'not_finished':
                        prev = content

                        try:
                            use_markdown = tokens != 'not_finished'
                            await edit_message_with_retry(context, chat_id, str(sent_message.message_id),
                                                          text=content, markdown=use_markdown)

                        except RetryAfter as e:
                            backoff += 5
                            await asyncio.sleep(e.retry_after)
                            continue

                        except TimedOut:
                            backoff += 5
                            await asyncio.sleep(0.5)
                            continue

                        except Exception:
                            backoff += 5
                            continue

                        await asyncio.sleep(0.01)

                    i += 1
                    if tokens != 'not_finished':
                        total_tokens = int(tokens)

                
            else:

                try:
                    interpretation, total_tokens = await self.openai.interpret_image(chat_id, temp_file_png, prompt=prompt)


                    try:
                        await update.effective_message.reply_text(
                            message_thread_id=get_thread_id(update),
                            reply_to_message_id=get_reply_to_message_id(self.config, update),
                            text=interpretation,
                            parse_mode=constants.ParseMode.MARKDOWN
                        )
                    except BadRequest:
                        try:
                            await update.effective_message.reply_text(
                                message_thread_id=get_thread_id(update),
                                reply_to_message_id=get_reply_to_message_id(self.config, update),
                                text=interpretation
                            )
                        except Exception as e:
                            logging.exception(e)
                            await update.effective_message.reply_text(
                                message_thread_id=get_thread_id(update),
                                reply_to_message_id=get_reply_to_message_id(self.config, update),
                                text=f"{localized_text('vision_fail', bot_language)}: {str(e)}",
                                parse_mode=constants.ParseMode.MARKDOWN
                            )
                except Exception as e:
                    logging.exception(e)
                    await update.effective_message.reply_text(
                        message_thread_id=get_thread_id(update),
                        reply_to_message_id=get_reply_to_message_id(self.config, update),
                        text=f"{localized_text('vision_fail', bot_language)}: {str(e)}",
                        parse_mode=constants.ParseMode.MARKDOWN
                    )
            vision_token_price = self.config['vision_token_price']
            self.usage[user_id].add_vision_tokens(total_tokens, vision_token_price)

            allowed_user_ids = self.config['allowed_user_ids'].split(',')
            if str(user_id) not in allowed_user_ids and 'guests' in self.usage:
                self.usage["guests"].add_vision_tokens(total_tokens, vision_token_price)

        await wrap_with_indicator(update, context, _execute, constants.ChatAction.TYPING)

    async def prompt(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """
        React to incoming messages and respond accordingly.
        """
        user_id = update.message.from_user.id
        add_newuser_db(user_id)
        if int(get_user_info_db(user_id)[2]) == 0 and int(get_user_info_db(user_id)[3] == 0):
            await update.message.reply_text("Превышен лимит запрсов, купите новые или оформите подписку, подробнее в /buy", disable_web_page_preview=True)
        else:
            prom(user_id)
            if update.edited_message or not update.message or update.message.via_bot:
                return

            if not await self.check_allowed_and_within_budget(update, context):
                return

            logging.info(
                f'New message received from user {update.message.from_user.name} (id: {update.message.from_user.id})')
            chat_id = update.effective_chat.id
            user_id = update.message.from_user.id
            prompt = message_text(update.message)
            self.last_message[chat_id] = prompt

            if is_group_chat(update):
                trigger_keyword = self.config['group_trigger_keyword']

                if prompt.lower().startswith(trigger_keyword.lower()) or update.message.text.lower().startswith('/chat'):
                    if prompt.lower().startswith(trigger_keyword.lower()):
                        prompt = prompt[len(trigger_keyword):].strip()

                    if update.message.reply_to_message and \
                            update.message.reply_to_message.text and \
                            update.message.reply_to_message.from_user.id != context.bot.id:
                        prompt = f'"{update.message.reply_to_message.text}" {prompt}'
                else:
                    if update.message.reply_to_message and update.message.reply_to_message.from_user.id == context.bot.id:
                        logging.info('Message is a reply to the bot, allowing...')
                    else:
                        logging.warning('Message does not start with trigger keyword, ignoring...')
                        return

            try:
                total_tokens = 0

                if self.config['stream']:
                    await update.effective_message.reply_chat_action(
                        action=constants.ChatAction.TYPING,
                        message_thread_id=get_thread_id(update)
                    )

                    stream_response = self.openai.get_chat_response_stream(chat_id=chat_id, query=prompt)
                    i = 0
                    prev = ''
                    sent_message = None
                    backoff = 0
                    stream_chunk = 0

                    async for content, tokens in stream_response:
                        if is_direct_result(content):
                            return await handle_direct_result(self.config, update, content)

                        if len(content.strip()) == 0:
                            continue

                        stream_chunks = split_into_chunks(content)
                        if len(stream_chunks) > 1:
                            content = stream_chunks[-1]
                            if stream_chunk != len(stream_chunks) - 1:
                                stream_chunk += 1
                                try:
                                    await edit_message_with_retry(context, chat_id, str(sent_message.message_id),
                                                              stream_chunks[-2])
                                except:
                                    pass
                                try:
                                    sent_message = await update.effective_message.reply_text(
                                        message_thread_id=get_thread_id(update),
                                        text=content if len(content) > 0 else "..."
                                    )
                                except:
                                    pass
                                continue

                        cutoff = get_stream_cutoff_values(update, content)
                        cutoff += backoff

                        if i == 0:
                            try:
                                if sent_message is not None:
                                    await context.bot.delete_message(chat_id=sent_message.chat_id,
                                                                 message_id=sent_message.message_id)
                                sent_message = await update.effective_message.reply_text(
                                    message_thread_id=get_thread_id(update),
                                    reply_to_message_id=get_reply_to_message_id(self.config, update),
                                    text=content,
                                )
                            except:
                                continue

                        elif abs(len(content) - len(prev)) > cutoff or tokens != 'not_finished':
                            prev = content

                            try:
                                use_markdown = tokens != 'not_finished'
                                await edit_message_with_retry(context, chat_id, str(sent_message.message_id),
                                                          text=content, markdown=use_markdown)

                            except RetryAfter as e:
                                backoff += 5
                                await asyncio.sleep(e.retry_after)
                                continue

                            except TimedOut:
                                backoff += 5
                                await asyncio.sleep(0.5)
                                continue

                            except Exception:
                                backoff += 5
                                continue

                            await asyncio.sleep(0.01)

                        i += 1
                        if tokens != 'not_finished':
                            total_tokens = int(tokens)

                else:
                    async def _reply():
                        nonlocal total_tokens
                        response, total_tokens = await self.openai.get_chat_response(chat_id=chat_id, query=prompt)

                        if is_direct_result(response):
                            return await handle_direct_result(self.config, update, response)

                        # Split into chunks of 4096 characters (Telegram's message limit)
                        chunks = split_into_chunks(response)

                        for index, chunk in enumerate(chunks):
                            try:
                                await update.effective_message.reply_text(
                                    message_thread_id=get_thread_id(update),
                                    reply_to_message_id=get_reply_to_message_id(self.config,
                                                                            update) if index == 0 else None,
                                    text=chunk,
                                    parse_mode=constants.ParseMode.MARKDOWN
                                )
                            except Exception:
                                try:
                                    await update.effective_message.reply_text(
                                        message_thread_id=get_thread_id(update),
                                        reply_to_message_id=get_reply_to_message_id(self.config,
                                                                                update) if index == 0 else None,
                                        text=chunk
                                    )
                                except Exception as exception:
                                    raise exception

                    await wrap_with_indicator(update, context, _reply, constants.ChatAction.TYPING)

                add_chat_request_to_usage_tracker(self.usage, self.config, user_id, total_tokens)

            except Exception as e:
                logging.exception(e)
                await update.effective_message.reply_text(
                    message_thread_id=get_thread_id(update),
                    reply_to_message_id=get_reply_to_message_id(self.config, update),
                    text=f"{localized_text('chat_fail', self.config['bot_language'])} {str(e)}",
                    parse_mode=constants.ParseMode.MARKDOWN
                )

    async def inline_query(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """
        Handle the inline query. This is run when you type: @botusername <query>
        """
        query = update.inline_query.query
        if len(query) < 3:
            return
        if not await self.check_allowed_and_within_budget(update, context, is_inline=True):
            return

        callback_data_suffix = "gpt:"
        result_id = str(uuid4())
        self.inline_queries_cache[result_id] = query
        callback_data = f'{callback_data_suffix}{result_id}'

        await self.send_inline_query_result(update, result_id, message_content=query, callback_data=callback_data)

    async def send_inline_query_result(self, update: Update, result_id, message_content, callback_data=""):
        """
        Send inline query result
        """
        try:
            reply_markup = None
            bot_language = self.config['bot_language']
            if callback_data:
                reply_markup = InlineKeyboardMarkup([[
                    InlineKeyboardButton(text=f'🤖 {localized_text("answer_with_chatgpt", bot_language)}',
                                         callback_data=callback_data)
                ]])

            inline_query_result = InlineQueryResultArticle(
                id=result_id,
                title=localized_text("ask_chatgpt", bot_language),
                input_message_content=InputTextMessageContent(message_content),
                description=message_content,
                thumb_url='https://user-images.githubusercontent.com/11541888/223106202-7576ff11-2c8e-408d-94ea'
                          '-b02a7a32149a.png',
                reply_markup=reply_markup
            )

            await update.inline_query.answer([inline_query_result], cache_time=0)
        except Exception as e:
            logging.error(f'An error occurred while generating the result card for inline query {e}')

    async def handle_callback_inline_query(self, update: Update, context: CallbackContext):
        """
        Handle the callback query from the inline query result
        """
        callback_data = update.callback_query.data
        user_id = update.callback_query.from_user.id
        inline_message_id = update.callback_query.inline_message_id
        name = update.callback_query.from_user.name
        callback_data_suffix = "gpt:"
        query = ""
        bot_language = self.config['bot_language']
        answer_tr = localized_text("answer", bot_language)
        loading_tr = localized_text("loading", bot_language)

        try:
            if callback_data.startswith(callback_data_suffix):
                unique_id = callback_data.split(':')[1]
                total_tokens = 0

                # Retrieve the prompt from the cache
                query = self.inline_queries_cache.get(unique_id)
                if query:
                    self.inline_queries_cache.pop(unique_id)
                else:
                    error_message = (
                        f'{localized_text("error", bot_language)}. '
                        f'{localized_text("try_again", bot_language)}'
                    )
                    await edit_message_with_retry(context, chat_id=None, message_id=inline_message_id,
                                                  text=f'{query}\n\n_{answer_tr}:_\n{error_message}',
                                                  is_inline=True)
                    return

                unavailable_message = localized_text("function_unavailable_in_inline_mode", bot_language)
                if self.config['stream']:
                    stream_response = self.openai.get_chat_response_stream(chat_id=user_id, query=query)
                    i = 0
                    prev = ''
                    backoff = 0
                    async for content, tokens in stream_response:
                        if is_direct_result(content):
                            cleanup_intermediate_files(content)
                            await edit_message_with_retry(context, chat_id=None,
                                                          message_id=inline_message_id,
                                                          text=f'{query}\n\n_{answer_tr}:_\n{unavailable_message}',
                                                          is_inline=True)
                            return

                        if len(content.strip()) == 0:
                            continue

                        cutoff = get_stream_cutoff_values(update, content)
                        cutoff += backoff

                        if i == 0:
                            try:
                                await edit_message_with_retry(context, chat_id=None,
                                                              message_id=inline_message_id,
                                                              text=f'{query}\n\n{answer_tr}:\n{content}',
                                                              is_inline=True)
                            except:
                                continue

                        elif abs(len(content) - len(prev)) > cutoff or tokens != 'not_finished':
                            prev = content
                            try:
                                use_markdown = tokens != 'not_finished'
                                divider = '_' if use_markdown else ''
                                text = f'{query}\n\n{divider}{answer_tr}:{divider}\n{content}'

                                # We only want to send the first 4096 characters. No chunking allowed in inline mode.
                                text = text[:4096]

                                await edit_message_with_retry(context, chat_id=None, message_id=inline_message_id,
                                                              text=text, markdown=use_markdown, is_inline=True)

                            except RetryAfter as e:
                                backoff += 5
                                await asyncio.sleep(e.retry_after)
                                continue
                            except TimedOut:
                                backoff += 5
                                await asyncio.sleep(0.5)
                                continue
                            except Exception:
                                backoff += 5
                                continue

                            await asyncio.sleep(0.01)

                        i += 1
                        if tokens != 'not_finished':
                            total_tokens = int(tokens)

                else:
                    async def _send_inline_query_response():
                        nonlocal total_tokens
                        # Edit the current message to indicate that the answer is being processed
                        await context.bot.edit_message_text(inline_message_id=inline_message_id,
                                                            text=f'{query}\n\n_{answer_tr}:_\n{loading_tr}',
                                                            parse_mode=constants.ParseMode.MARKDOWN)

                        logging.info(f'Generating response for inline query by {name}')
                        response, total_tokens = await self.openai.get_chat_response(chat_id=user_id, query=query)

                        if is_direct_result(response):
                            cleanup_intermediate_files(response)
                            await edit_message_with_retry(context, chat_id=None,
                                                          message_id=inline_message_id,
                                                          text=f'{query}\n\n_{answer_tr}:_\n{unavailable_message}',
                                                          is_inline=True)
                            return

                        text_content = f'{query}\n\n_{answer_tr}:_\n{response}'

                        # We only want to send the first 4096 characters. No chunking allowed in inline mode.
                        text_content = text_content[:4096]

                        # Edit the original message with the generated content
                        await edit_message_with_retry(context, chat_id=None, message_id=inline_message_id,
                                                      text=text_content, is_inline=True)

                    await wrap_with_indicator(update, context, _send_inline_query_response,
                                              constants.ChatAction.TYPING, is_inline=True)

                add_chat_request_to_usage_tracker(self.usage, self.config, user_id, total_tokens)

        except Exception as e:
            logging.error(f'Failed to respond to an inline query via button callback: {e}')
            logging.exception(e)
            localized_answer = localized_text('chat_fail', self.config['bot_language'])
            await edit_message_with_retry(context, chat_id=None, message_id=inline_message_id,
                                          text=f"{query}\n\n_{answer_tr}:_\n{localized_answer} {str(e)}",
                                          is_inline=True)

    async def check_allowed_and_within_budget(self, update: Update, context: ContextTypes.DEFAULT_TYPE,
                                              is_inline=False) -> bool:
        """
        Checks if the user is allowed to use the bot and if they are within their budget
        :param update: Telegram update object
        :param context: Telegram context object
        :param is_inline: Boolean flag for inline queries
        :return: Boolean indicating if the user is allowed to use the bot
        """
        name = update.inline_query.from_user.name if is_inline else update.message.from_user.name
        user_id = update.inline_query.from_user.id if is_inline else update.message.from_user.id

        if not await is_allowed(self.config, update, context, is_inline=is_inline):
            logging.warning(f'User {name} (id: {user_id}) is not allowed to use the bot')
            await self.send_disallowed_message(update, context, is_inline)
            return False
        if not is_within_budget(self.config, self.usage, update, is_inline=is_inline):
            logging.warning(f'User {name} (id: {user_id}) reached their usage limit')
            await self.send_budget_reached_message(update, context, is_inline)
            return False

        return True

    async def send_disallowed_message(self, update: Update, _: ContextTypes.DEFAULT_TYPE, is_inline=False):
        """
        Sends the disallowed message to the user.
        """
        if not is_inline:
            await update.effective_message.reply_text(
                message_thread_id=get_thread_id(update),
                text="Произошла непредвиденная ошибка, обратитесь в поддержку(",
                disable_web_page_preview=True
            )
        else:
            result_id = str(uuid4())
            await self.send_inline_query_result(update, result_id, message_content=self.disallowed_message)

    async def send_budget_reached_message(self, update: Update, _: ContextTypes.DEFAULT_TYPE, is_inline=False):
        """
        Sends the budget reached message to the user.
        """
        if not is_inline:
            await update.effective_message.reply_text(
                message_thread_id=get_thread_id(update),
                text="Произошла непредвиденная ошибка, обратитесь в поддержку("
            )
        else:
            result_id = str(uuid4())
            await self.send_inline_query_result(update, result_id, message_content=self.budget_limit_message)

    async def post_init(self, application: Application) -> None:
        """
        Post initialization hook for the bot.
        """
        await application.bot.set_my_commands(self.group_commands, scope=BotCommandScopeAllGroupChats())
        await application.bot.set_my_commands(self.commands)


    def run(self):
        """
        Runs the bot indefinitely until the user presses Ctrl+C
        """
        application = ApplicationBuilder() \
            .token(self.config['token']) \
            .proxy_url(self.config['proxy']) \
            .get_updates_proxy_url(self.config['proxy']) \
            .post_init(self.post_init) \
            .concurrent_updates(True) \
            .build()

        application.add_handler(CommandHandler('reset', self.reset))
        application.add_handler(CommandHandler('help', self.help))
        application.add_handler(CommandHandler('image', self.image))
        application.add_handler(CommandHandler('tts', self.tts))
        application.add_handler(CommandHandler('start', self.start_s))
        application.add_handler(CommandHandler('resend', self.resend))
        application.add_handler(CommandHandler('menu', self.menu_s))
        application.add_handler(CommandHandler('buy', self.buy))
        application.add_handler(CommandHandler('privacy', self.faq))
        application.add_handler(CommandHandler(
            'chat', self.prompt, filters=filters.ChatType.GROUP | filters.ChatType.SUPERGROUP)
        )
        application.add_handler(MessageHandler(
            filters.PHOTO | filters.Document.IMAGE,
            self.vision))
        application.add_handler(MessageHandler(
            filters.AUDIO | filters.VOICE | filters.Document.AUDIO |
            filters.VIDEO | filters.VIDEO_NOTE | filters.Document.VIDEO,
            self.transcribe))
        application.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), self.prompt))
        application.add_handler(InlineQueryHandler(self.inline_query, chat_types=[
            constants.ChatType.GROUP, constants.ChatType.SUPERGROUP, constants.ChatType.PRIVATE
        ]))
        application.add_handler(CallbackQueryHandler(self.button_handler))
        application.add_handler(PreCheckoutQueryHandler(self.pre_checkout_callback))
        #application.add_handler(CallbackQueryHandler(self.handle_callback_inline_query))
        application.add_handler(MessageHandler(filters.SuccessfulPayment(), self.successful_payment_s))
        application.add_error_handler(error_handler)
        application.run_polling()  # Запускаем бота





