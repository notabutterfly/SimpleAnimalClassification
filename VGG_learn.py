# Библиотеки для обучения сетей
import torch
import torch.nn as nn
from tqdm.notebook import tqdm as tqdm_notebook
import torch.nn.functional as F
import matplotlib.pyplot as plt
# Модули библиотеки PyTorch
from torchvision import datasets, transforms, models
# Метрика качества
from sklearn.metrics import accuracy_score

import os
import numpy as np

# Дообучение сети vgg16

transform_train = transforms.Compose([
    transforms.Resize((100, 100)),
    transforms.ToTensor(),
    transforms.Normalize(mean=[0.485, 0.456, 0.406],
                         std=[0.229, 0.224, 0.225])
])

transform_val = transforms.Compose([
    transforms.Resize((100, 100)),
    transforms.ToTensor(),
    transforms.Normalize(mean=[0.485, 0.456, 0.406],
                         std=[0.229, 0.224, 0.225])
])


train_data = datasets.ImageFolder("C:/Datasets/animals/train", transform=transform_train)
test_data = datasets.ImageFolder("C:/Datasets/animals/val", transform=transform_val)


train_loader = torch.utils.data.DataLoader(train_data, batch_size=64, shuffle= True)
test_loader = torch.utils.data.DataLoader(test_data, batch_size=len(test_data), shuffle= False)


 #посмотреть на картинки
dataiter = iter(train_loader)
# батч картинок и батч ответов к картинкам
images, labels = next(dataiter)

"""
def show_imgs(imgs, labels):
  f, axes = plt.subplots(1, 10, figsize=(30,5))
  for i, axis in enumerate(axes):
    axes[i].imshow(np.squeeze(np.transpose(imgs[i].numpy(), (1, 2, 0))), cmap="gray")
    axes[i].set_title(labels[i].numpy())
  plt.show()

show_imgs(images, labels)
"""

vgg16 = models.vgg16(pretrained=True)
vgg16.classifier = nn.Sequential(*list(vgg16.classifier.children()))[:-1]
class New_VGG16(nn.Module):
    def __init__(self, model):
        super().__init__()
        self.vgg16 = model
        for param in self.vgg16.features.parameters():
            param.requires_grad = False
        self.fc = nn.Linear(4096, 5)

    def forward(self, x):
        # forward pass сети
        # умнож на матрицу весов 1 слоя и применение функции активации
        x = self.vgg16(x)
        x = self.fc(x)
        return x


"""
def train(net, n_epoch=5):
    try:
        # выбираем функцию потерь
        loss_fn = torch.nn.CrossEntropyLoss()

        # выбираем алгоритм оптимизации и learning_rate
        learning_rate = 1e-3
        optimizer = torch.optim.Adam(net.parameters(), lr=learning_rate)

        # обучаем сеть 5 эпохи
        for epoch in tqdm_notebook(range(n_epoch)):

            running_loss = 0.0
            train_dataiter = iter(train_loader)
            for i, batch in enumerate(tqdm_notebook(train_dataiter)):
                # так получаем текущий батч
                X_batch, y_batch = batch

                # обнуляем веса
                optimizer.zero_grad()

                #forward pass (получение ответов на батч картинок)
                y_pred = net(X_batch)
                # Вычисление лосса от выданных сетью ответов и правильных ответов на батч
                loss = loss_fn(y_pred, y_batch)
                # bsckpropagation (вычисление градиентов)
                loss.backward()
                # обновление весов сети
                optimizer.step()

                # текущий лосс
                running_loss += loss.item()
                # выведем качество каждые 500 батчей
                if i % 10 == 9:
                    print('[%d, %5d] loss: %.3f, acc: %3f' %
                        (epoch + 1, i + 1, running_loss / 500, accuracy_score(y_batch.numpy(), np.argmax(y_pred.detach().numpy(), axis=1))))
                    running_loss = 0.0
    except KeyError:
        pass
    print('Обучение закончено')
    return net
"""

net = New_VGG16(vgg16)
#train(net)


#print(accuracy_score(labels.numpy(), np.argmax(net.forward(images).detach().numpy(), axis=1)))

torch.save(net.state_dict(), "my_vgg.pth")





