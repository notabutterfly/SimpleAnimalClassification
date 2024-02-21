import torch
import torch.nn as nn
import torch.nn.functional as F
import torchvision.io
import matplotlib.pyplot as plt
from matplotlib import image
from torchvision import datasets, transforms, models
from PIL import Image
import torchvision.transforms as transforms



import os
import numpy as np
from modules.VGG_learn import New_VGG16



vgg16 = models.vgg16(pretrained=True)
vgg16.classifier = nn.Sequential(*list(vgg16.classifier.children()))[:-1]
model = New_VGG16(vgg16)

model.load_state_dict(torch.load("modules/my_vgg.pth"))


transform = transforms.Compose([
    transforms.Resize((100, 100)),
    transforms.ToTensor(),
    transforms.Normalize(mean=[0.485, 0.456, 0.406],
                         std=[0.229, 0.224, 0.225])
])

img = Image.open("elephant2.jpg")
img_t = transform(img)
batch_t = torch.unsqueeze(img_t, 0)

label = {0 : "cat", 1: "dog", 2: "elephant", 3: "horse", 4: "lion"}
model.eval()
out = model(batch_t)
print(f"it is a {label[int(torch.max(out, 1).indices)]}")