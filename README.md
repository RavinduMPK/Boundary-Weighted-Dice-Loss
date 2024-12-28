# Boundary-Weighted Dice Loss
This repo holds code for Boundary-Weighted Dice Loss For Medical Image Segmentation.

## Usage

### 1. Download Google pre-trained ViT models
* [Get models in this link](https://console.cloud.google.com/storage/vit_models/): R50-ViT-B_16, ViT-B_16, ViT-L_16...
```bash
wget [https://storage.googleapis.com/vit_models/imagenet21k](https://console.cloud.google.com/storage/browser/vit_models;tab=objects?inv=1&invt=AblUoA&prefix=&forceOnObjectsSortingFiltering=false)/{MODEL_NAME}.npz &&
mkdir ../model/vit_checkpoint/imagenet21k &&
mv {MODEL_NAME}.npz ../model/vit_checkpoint/imagenet21k/{MODEL_NAME}.npz
```

### 2. Dataset
You can follow [TransUnet](https://github.com/Beckschen/TransUNet/blob/main/datasets/README.md) to get and prepare the datasets.

2. The directory structure of the whole project is as follows:

```bash
.
├── TransUNet
│   └── 
├── model
│   └── vit_checkpoint
│       └── imagenet21k
│           ├── R50+ViT-B_16.npz
│           └── *.npz
├── Synapse
│   ├── test
│   │   ├── case0001.npy.h5
│   │   └── *.npy.h5
│   ├── train
│   │   ├── case0005_slice000.npz
│   │   └── *.npz
│   └── lists_Synapse
│       ├── all.lst
│       ├── test.txt
│       └── train.txt
└── ACDC
    └── ...(same as Synapse)
```

### 2. Environment
Please prepare an environment with python=3.11, and then use the command "pip install -r requirements.txt" for the dependencies.
Execute inside TransUNet folder.

### 3. Train/Test
1. For Synapse dataset
* train
```bash
CUDA_VISIBLE_DEVICES=0 python train.py --dataset Synapse --vit_name R50-ViT-B_16
```

* test
```bash
CUDA_VISIBLE_DEVICES=0 python test.py --dataset Synapse --vit_name R50-ViT-B_16 --is_savenii
```

2. For ACDC dataset
* train
```bash
CUDA_VISIBLE_DEVICES=0 python train.py --dataset ACDC --vit_name R50-ViT-B_16
```

* test
```bash
CUDA_VISIBLE_DEVICES=0 python test.py --dataset ACDC --vit_name R50-ViT-B_16 --is_savenii
```


## Reference
* [Boundary Difference over Union (MICCAI 2023)](https://conferences.miccai.org/2023/papers/093-Paper1247.html)
* [TransUNet](https://github.com/Beckschen/TransUNet)


