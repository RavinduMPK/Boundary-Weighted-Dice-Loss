import numpy as np
import torch
from medpy import metric
from scipy.ndimage import zoom
import torch.nn as nn
import SimpleITK as sitk
import torch.nn.functional as F
from scipy.ndimage import distance_transform_edt as distance
from skimage import segmentation as skimage_seg

class TverskyLoss(nn.Module):
    def __init__(self, classes) -> None:
        super().__init__()
        self.classes = classes

    def _one_hot_encoder(self, input_tensor):
        tensor_list = []
        for i in range(self.classes):
            temp_prob = input_tensor == i  # * torch.ones_like(input_tensor)
            tensor_list.append(temp_prob.unsqueeze(1))
        output_tensor = torch.cat(tensor_list, dim=1)
        return output_tensor.float()

    def forward(self, y_pred, y_true, alpha=0.7, beta=0.3):

        y_pred = torch.softmax(y_pred, dim=1)
        y_true = self._one_hot_encoder(y_true)
        loss = 0
        for i in range(1, self.classes):
            p0 = y_pred[:, i, :, :]
            ones = torch.ones_like(p0)
            #p1: prob that the pixel is of class 0
            p1 = ones - p0  
            g0 = y_true[:, i, :, :]
            g1 = ones - g0
            #terms in the Tversky loss function combined with weights
            tp = torch.sum(p0 * g0)
            fp = alpha * torch.sum(p0 * g1)
            fn = beta * torch.sum(p1 * g0)
            #add to the denominator a small epsilon to prevent the value from being undefined 
            EPS = 1e-5
            num = tp
            den = tp + fp + fn + EPS
            result = num / den
            loss += result
        return 1 - loss / self.classes


class BoundaryLoss(nn.Module):
    # def __init__(self, **kwargs):
    def __init__(self, classes) -> None:
        super().__init__()
        # # Self.idc is used to filter out some classes of the target mask. Use fancy indexing
        # self.idc: List[int] = kwargs["idc"]
        self.idx = [i for i in range(classes)]

    def compute_sdf1_1(self, img_gt, out_shape):
        """
        compute the normalized signed distance map of binary mask
        input: segmentation, shape = (batch_size, x, y, z)
        output: the Signed Distance Map (SDM) 
        sdf(x) = 0; x in segmentation boundary
                -inf|x-y|; x in segmentation
                +inf|x-y|; x out of segmentation
        normalize sdf to [-1, 1]
        """
        img_gt = img_gt.cpu().numpy()
        img_gt = img_gt.astype(np.uint8)

        normalized_sdf = np.zeros(out_shape)

        for b in range(out_shape[0]): # batch size
                # ignore background
            for c in range(1, out_shape[1]):
                posmask = img_gt[b].astype(np.bool)
                if posmask.any():
                    negmask = ~posmask
                    posdis = distance(posmask)
                    negdis = distance(negmask)
                    boundary = skimage_seg.find_boundaries(posmask, mode='inner').astype(np.uint8)
                    sdf = (negdis-np.min(negdis))/(np.max(negdis)-np.min(negdis)) - (posdis-np.min(posdis))/(np.max(posdis)-np.min(posdis))
                    sdf[boundary==1] = 0
                    normalized_sdf[b][c] = sdf

        return normalized_sdf

    def forward(self, outputs, gt):
        """
        compute boundary loss for binary segmentation
        input: outputs_soft: sigmoid results,  shape=(b,2,x,y,z)
            gt_sdf: sdf of ground truth (can be original or normalized sdf); shape=(b,2,x,y,z)
        output: boundary_loss; sclar
        """
        outputs_soft = F.softmax(outputs, dim=1)
        gt_sdf = self.compute_sdf1_1(gt, outputs_soft.shape)
        pc = outputs_soft[:,self.idx,...]
        dc = torch.from_numpy(gt_sdf[:,self.idx,...]).cuda()
        multipled = torch.einsum('bxyz, bxyz->bxyz', pc, dc)
        bd_loss = multipled.mean()

        return bd_loss


class DiceLoss(nn.Module):
    def __init__(self, n_classes):
        super(DiceLoss, self).__init__()
        self.n_classes = n_classes

    def _one_hot_encoder(self, input_tensor):
        tensor_list = []
        for i in range(self.n_classes):
            temp_prob = input_tensor == i  # * torch.ones_like(input_tensor)
            tensor_list.append(temp_prob.unsqueeze(1))
        output_tensor = torch.cat(tensor_list, dim=1)
        return output_tensor.float()

    def _dice_loss(self, score, target):
        target = target.float()
        smooth = 1e-5
        intersect = torch.sum(score * target)
        y_sum = torch.sum(target * target)
        z_sum = torch.sum(score * score)
        loss = (2 * intersect + smooth) / (z_sum + y_sum + smooth)
        loss = 1 - loss
        return loss

    def forward(self, inputs, target, weight=None, softmax=False):
        if softmax:
            inputs = torch.softmax(inputs, dim=1)
        target = self._one_hot_encoder(target)
        if weight is None:
            weight = [1] * self.n_classes
        assert inputs.size() == target.size(), 'predict {} & target {} shape do not match'.format(inputs.size(), target.size())
        class_wise_dice = []
        loss = 0.0
        for i in range(0, self.n_classes):
            dice = self._dice_loss(inputs[:, i], target[:, i])
            class_wise_dice.append(1.0 - dice.item())
            loss += dice * weight[i]
        return loss / self.n_classes

import cv2
def mask_to_boundary(mask, dilation_ratio=0.02):
    """
    Convert binary mask to boundary mask.
    :param mask (numpy array, uint8): binary mask
    :param dilation_ratio (float): ratio to calculate dilation = dilation_ratio * image_diagonal
    :return: boundary mask (numpy array)
    """

    h, w = mask.shape
    img_diag = np.sqrt(h ** 2 + w ** 2)
    dilation = int(round(dilation_ratio * img_diag))
    if dilation < 1:
        dilation = 1
    # Pad image so mask truncated by the image border is also considered as boundary.
    new_mask = cv2.copyMakeBorder(mask, 1, 1, 1, 1, cv2.BORDER_CONSTANT, value=0)
    kernel = np.ones((3, 3), dtype=np.uint8)
    new_mask_erode = cv2.erode(new_mask, kernel, iterations=dilation)
    mask_erode = new_mask_erode[1 : h + 1, 1 : w + 1]
    # G_d intersects G in the paper.
    return mask - mask_erode

def _adaptive_size(target):
    target = torch.from_numpy(target).float()
    kernel = torch.Tensor([[0, 1, 0], [1, 1, 1], [0, 1, 0]])
    padding_out = torch.zeros((target.shape[0], target.shape[-2] + 2, target.shape[-1] + 2))
    padding_out[:, 1:-1, 1:-1] = target
    h, w = 3, 3

    Y = torch.zeros((padding_out.shape[0], padding_out.shape[1] - h + 1, padding_out.shape[2] - w + 1))
    for i in range(Y.shape[0]):
        Y[i, :, :] = torch.conv2d(target[i].unsqueeze(0).unsqueeze(0), kernel.unsqueeze(0).unsqueeze(0),
                                  padding=1)

    Y = Y * target
    Y[Y == 5] = 0
    C = torch.count_nonzero(Y)
    S = torch.count_nonzero(target)
    smooth = 1e-5
    return (C + smooth)/(S + smooth).item()

def calculate_metric_percase(pred, gt):
    pred[pred > 0] = 1
    gt[gt > 0] = 1
    smooth = 1e-5
    boundary_IOU = 0
    for i in range(pred.squeeze().shape[0]):
        pred_boundary = mask_to_boundary(np.uint8(pred[i].squeeze()))
        gt_boundary = mask_to_boundary(np.uint8(gt[i].squeeze()))
        boundary_inter = np.sum(pred_boundary * gt_boundary)
        boundary_union = np.sum(pred_boundary + gt_boundary) - boundary_inter
        boundary_IOU += (boundary_inter + smooth) / (boundary_union + smooth) / pred.squeeze().shape[0]
    if pred.sum() > 0 and gt.sum()>0:
        dice = metric.binary.dc(pred, gt)
        hd95 = metric.binary.hd95(pred, gt)
        return dice, hd95, boundary_IOU
    elif pred.sum() > 0 and gt.sum()==0:
        return 1, 0, boundary_IOU
    else:
        return 0, 0, boundary_IOU

def get_map(mask):
        if len(np.unique(mask)) <= 3:
            mapping = {
                0: 0,
                1: 128,
                2: 255
            }
        else:
            mapping = {0: 0,
                    1: 85,
                    # 128: 2,
                    2: 170,
                    3: 255}
        for k in mapping:
            mask[mask == k] = mapping[k]
        return mask

def test_single_volume(image, label, net, classes, patch_size=[256, 256], test_save_path=None, case=None, z_spacing=1):
    image, label = image.squeeze(0).cpu().detach().numpy(), label.squeeze(0).cpu().detach().numpy()
    if len(image.shape) == 3:
        prediction = np.zeros_like(label)
        for ind in range(image.shape[0]):
            slice = image[ind, :, :]
            x, y = slice.shape[0], slice.shape[1]
            if x != patch_size[0] or y != patch_size[1]:
                slice = zoom(slice, (patch_size[0] / x, patch_size[1] / y), order=3)  # previous using 0
            input = torch.from_numpy(slice).unsqueeze(0).unsqueeze(0).float().cuda()
            net.eval()
            with torch.no_grad():
                outputs = net(input)
                out = torch.argmax(torch.softmax(outputs, dim=1), dim=1).squeeze(0)
                out = out.cpu().detach().numpy()
                if x != patch_size[0] or y != patch_size[1]:
                    pred = zoom(out, (x / patch_size[0], y / patch_size[1]), order=0)
                else:
                    pred = out
                prediction[ind] = pred
    else:
        input = torch.from_numpy(image).unsqueeze(
            0).unsqueeze(0).float().cuda()
        net.eval()
        with torch.no_grad():
            out = torch.argmax(torch.softmax(net(input), dim=1), dim=1).squeeze(0)
            prediction = out.cpu().detach().numpy()
    metric_list = []
    big_list = []
    small_list = []
    for i in range(1, classes):
        metric_list.append(calculate_metric_percase(prediction == i, label == i))
        if _adaptive_size(label == i) >= 0.2:
            small_list.append(metric_list[-1][0])
        else:
            big_list.append(metric_list[-1][0])

    if test_save_path is not None:
        img_itk = sitk.GetImageFromArray(image.astype(np.float32))
        prd_itk = sitk.GetImageFromArray(prediction.astype(np.float32))
        lab_itk = sitk.GetImageFromArray(label.astype(np.float32))
        img_itk.SetSpacing((1, 1, z_spacing))
        prd_itk.SetSpacing((1, 1, z_spacing))
        lab_itk.SetSpacing((1, 1, z_spacing))
        sitk.WriteImage(prd_itk, test_save_path + '/'+case + "_pred.nii.gz")
        sitk.WriteImage(img_itk, test_save_path + '/'+ case + "_img.nii.gz")
        sitk.WriteImage(lab_itk, test_save_path + '/'+ case + "_gt.nii.gz")
    return metric_list, big_list, small_list


class BoundaryDoULoss(nn.Module):
    def __init__(self, n_classes):
        super(BoundaryDoULoss, self).__init__()
        self.n_classes = n_classes

    def _one_hot_encoder(self, input_tensor):
        tensor_list = []
        for i in range(self.n_classes):
            temp_prob = input_tensor == i
            tensor_list.append(temp_prob.unsqueeze(1))
        output_tensor = torch.cat(tensor_list, dim=1)
        return output_tensor.float()

    def _adaptive_size(self, score, target):
        kernel = torch.Tensor([[0,1,0], [1,1,1], [0,1,0]])
        padding_out = torch.zeros((target.shape[0], target.shape[-2]+2, target.shape[-1]+2))
        padding_out[:, 1:-1, 1:-1] = target
        h, w = 3, 3

        Y = torch.zeros((padding_out.shape[0], padding_out.shape[1] - h + 1, padding_out.shape[2] - w + 1)).cuda()
        for i in range(Y.shape[0]):
            Y[i, :, :] = torch.conv2d(target[i].unsqueeze(0).unsqueeze(0), kernel.unsqueeze(0).unsqueeze(0).cuda(), padding=1)
        Y = Y * target
        Y[Y == 5] = 0
        C = torch.count_nonzero(Y)
        S = torch.count_nonzero(target)
        smooth = 1e-5
        alpha = 1 - (C + smooth) / (S + smooth)
        alpha = 2 * alpha - 1

        intersect = torch.sum(score * target)
        y_sum = torch.sum(target * target)
        z_sum = torch.sum(score * score)
        alpha = min(alpha, 0.8)  ## We recommend using a truncated alpha of 0.8, as using truncation gives better results on some datasets and has rarely effect on others.
        loss = (z_sum + y_sum - 2 * intersect + smooth) / (z_sum + y_sum - (1 + alpha) * intersect + smooth)

        return loss

    def forward(self, inputs, target):
        inputs = torch.softmax(inputs, dim=1)
        target = self._one_hot_encoder(target)

        assert inputs.size() == target.size(), 'predict {} & target {} shape do not match'.format(inputs.size(), target.size())

        loss = 0.0
        for i in range(0, self.n_classes):
            loss += self._adaptive_size(inputs[:, i], target[:, i])
        return loss / self.n_classes
    
    

class DiceLoss(nn.Module):
    def __init__(self, n_classes):
        super(DiceLoss, self).__init__()
        self.n_classes = n_classes

    def _one_hot_encoder(self, input_tensor):
        tensor_list = []
        for i in range(self.n_classes):
            temp_prob = input_tensor == i  # * torch.ones_like(input_tensor)
            tensor_list.append(temp_prob.unsqueeze(1))
        output_tensor = torch.cat(tensor_list, dim=1)
        return output_tensor.float()

    def _dice_loss(self, score, target):
        target = target.float()
        smooth = 1e-5
        intersect = torch.sum(score * target)
        y_sum = torch.sum(target * target)
        z_sum = torch.sum(score * score)
        loss = (2 * intersect + smooth) / (z_sum + y_sum + smooth)
        loss = 1 - loss
        return loss

    def forward(self, inputs, target, weight=None, softmax=False):
        if softmax:
            inputs = torch.softmax(inputs, dim=1)
        target = self._one_hot_encoder(target)
        if weight is None:
            weight = [1] * self.n_classes
        assert inputs.size() == target.size(), 'predict {} & target {} shape do not match'.format(inputs.size(), target.size())
        class_wise_dice = []
        loss = 0.0
        for i in range(0, self.n_classes):
            dice = self._dice_loss(inputs[:, i], target[:, i])
            class_wise_dice.append(1.0 - dice.item())
            loss += dice * weight[i]
        return loss / self.n_classes
        


class BoundaryWeightedDiceLoss(nn.Module):
    def __init__(self, n_classes, lambda_weight=1.0):
        """
        Initialize the BoundaryWeightedDiceLoss class.

        Args:
            n_classes (int): Number of classes for segmentation.
            lambda_weight (float): Weight for the boundary-sensitive term.
        """
        super(BoundaryWeightedDiceLoss, self).__init__()
        self.n_classes = n_classes
        self.lambda_weight = lambda_weight

    def _one_hot_encoder(self, input_tensor):
        """
        One-hot encode the input tensor.
        
        Args:
            input_tensor (torch.Tensor): Ground truth tensor with shape (B, H, W).
        
        Returns:
            torch.Tensor: One-hot encoded tensor with shape (B, C, H, W).
        """
        tensor_list = []
        for i in range(self.n_classes):
            temp_prob = input_tensor == i
            tensor_list.append(temp_prob.unsqueeze(1))
        output_tensor = torch.cat(tensor_list, dim=1)
        return output_tensor.float()

    def dice_loss(self, y_pred, y_true, smooth=1e-5):
        """
        Compute the per-class Dice loss and average across classes.
        """
        dice_per_class = []
        for c in range(self.n_classes):
            intersection = torch.sum(y_pred[:, c] * y_true[:, c])
            union = torch.sum(y_pred[:, c]) + torch.sum(y_true[:, c])
            dice = (2.0 * intersection + smooth) / (union + smooth)
            dice_per_class.append(1 - dice)
        
        return torch.mean(torch.stack(dice_per_class))


    def gradient(self, tensor):
        """
        Compute gradient using Sobel filters for each channel.
        
        Args:
            tensor (torch.Tensor): Input tensor with shape (B, C, H, W).
        
        Returns:
            torch.Tensor: Gradient magnitude tensor with shape (B, C, H, W).
        """
        sobel_x = torch.tensor([[1, 0, -1], [2, 0, -2], [1, 0, -1]], dtype=torch.float32, device=tensor.device).view(1, 1, 3, 3)
        sobel_y = torch.tensor([[1, 2, 1], [0, 0, 0], [-1, -2, -1]], dtype=torch.float32, device=tensor.device).view(1, 1, 3, 3)
        
        gradients = []
        for c in range(tensor.shape[1]):  # Process each channel separately
            channel = tensor[:, c:c+1, :, :]  # Extract one channel (B, 1, H, W)
            grad_x = F.conv2d(channel, sobel_x, padding=1)
            grad_y = F.conv2d(channel, sobel_y, padding=1)
            grad_magnitude = torch.sqrt(grad_x ** 2 + grad_y ** 2 + 1e-5)
            gradients.append(grad_magnitude)
        
        return torch.cat(gradients, dim=1)  # Combine gradients for all channels (B, C, H, W)

    def boundary_term(self, y_pred, y_true):
        """
        Compute the boundary-sensitive term with overlap consideration.
        """
        grad_pred = self.gradient(y_pred)
        grad_true = self.gradient(y_true)
        boundary_loss = torch.mean(torch.abs(grad_true - grad_pred))
        
        # Add overlap term to emphasize boundary region matching
        overlap = torch.sum(grad_pred * grad_true) / (torch.sum(grad_pred) + torch.sum(grad_true) + 1e-5)
        return boundary_loss + (1 - overlap)
        

    def forward(self, y_pred, y_true):
        """
        Compute the Boundary-Weighted Dice Loss.
        
        Args:
            y_pred (torch.Tensor): Predicted tensor with shape (B, C, H, W).
            y_true (torch.Tensor): Ground truth tensor with shape (B, H, W).
        
        Returns:
            torch.Tensor: Combined loss value.
        """
        y_pred = torch.softmax(y_pred, dim=1)  # Apply softmax to predictions
        y_true_one_hot = self._one_hot_encoder(y_true)  # Use custom one-hot encoder

        dice = self.dice_loss(y_pred, y_true_one_hot)
        boundary = self.boundary_term(y_pred[:, 1:], y_true_one_hot[:, 1:])  # Ignore background class for boundary term
        return dice + self.lambda_weight * boundary