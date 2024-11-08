import math
import operator
import os
import zipfile
from pathlib import Path
from time import time
from tkinter import Tcl
from typing import Union
from time import time, sleep

import cv2
import matplotlib.pyplot as plt
import moviepy.video.io.ImageSequenceClip
import nibabel as nib
import numpy as np
import pandas as pd
import pydicom
import wget
from totalsegmentator.libs import nostdout
import re
import subprocess
import zipfile

from comp2comp.inference_class_base import InferenceClass
import pydicom
import numpy as np
import cv2
import os
import pandas as pd
from typing import Union
import matplotlib.pyplot as plt
from moviepy.video.io.ImageSequenceClip import ImageSequenceClip
import math
import operator
from pathlib import Path
from comp2comp.inference_class_base import InferenceClass
import shutil

class AortaSegmentation(InferenceClass):
    """Spine segmentation."""

    def __init__(self, save=True):
        super().__init__()
        self.model_name = "totalsegmentator"
        self.save_segmentations = save

    def __call__(self, inference_pipeline):
        # inference_pipeline.dicom_series_path = self.input_path
        self.output_dir = inference_pipeline.output_dir
        self.output_dir_segmentations = os.path.join(self.output_dir, "segmentations/")
        if not os.path.exists(self.output_dir_segmentations):
            os.makedirs(self.output_dir_segmentations)

        self.model_dir = inference_pipeline.model_dir

        seg, mv = self.spine_seg(
            os.path.join(self.output_dir_segmentations, "converted_dcm.nii.gz"),
            self.output_dir_segmentations + "spine.nii.gz",
            inference_pipeline.model_dir,
        )

        seg = seg.get_fdata()
        medical_volume = mv.get_fdata()

        axial_masks = []
        ct_image = []

        for i in range(seg.shape[2]):
            axial_masks.append(seg[:, :, i])

        for i in range(medical_volume.shape[2]):
            ct_image.append(medical_volume[:, :, i])

        # Save input axial slices to pipeline
        inference_pipeline.ct_image = ct_image

        # Save aorta masks to pipeline
        inference_pipeline.axial_masks = axial_masks

        return {}

    def setup_nnunet_c2c(self, model_dir: Union[str, Path]):
        """Adapted from TotalSegmentator."""

        model_dir = Path(model_dir)
        config_dir = model_dir / Path("." + self.model_name)
        (config_dir / "nnunet/results/nnUNet/3d_fullres").mkdir(
            exist_ok=True, parents=True
        )
        (config_dir / "nnunet/results/nnUNet/2d").mkdir(exist_ok=True, parents=True)
        weights_dir = config_dir / "nnunet/results"
        self.weights_dir = weights_dir

        os.environ["nnUNet_raw_data_base"] = str(
            weights_dir
        )  # not needed, just needs to be an existing directory
        os.environ["nnUNet_preprocessed"] = str(
            weights_dir
        )  # not needed, just needs to be an existing directory
        os.environ["RESULTS_FOLDER"] = str(weights_dir)

    def download_spine_model(self, model_dir: Union[str, Path]):
        download_dir = Path(
            os.path.join(
                self.weights_dir,
                "nnUNet/3d_fullres/Task253_Aorta/nnUNetTrainerV2_ep4000_nomirror__nnUNetPlansv2.1",
            )
        )
        print(download_dir)
        fold_0_path = download_dir / "fold_1"
        if not os.path.exists(fold_0_path):
            download_dir.mkdir(parents=True, exist_ok=True)
            wget.download(
                "https://huggingface.co/AdritRao/aaav3/resolve/main/fold_1.zip",
                out=os.path.join(download_dir, "fold_1.zip"),
            )
            with zipfile.ZipFile(
                os.path.join(download_dir, "fold_1.zip"), "r"
            ) as zip_ref:
                zip_ref.extractall(download_dir)
            os.remove(os.path.join(download_dir, "fold_1.zip"))
            wget.download(
                "https://huggingface.co/AdritRao/aaav3/resolve/main/plans.pkl",
                out=os.path.join(download_dir, "plans.pkl"),
            )
            print("Spine model downloaded.")
        else:
            print("Spine model already downloaded.")

    def spine_seg(
        self, input_path: Union[str, Path], output_path: Union[str, Path], model_dir
    ):
        """Run spine segmentation.

        Args:
            input_path (Union[str, Path]): Input path.
            output_path (Union[str, Path]): Output path.
        """

        print("Segmenting spine...")
        st = time()
        os.environ["SCRATCH"] = self.model_dir

        print(self.model_dir)

        # Setup nnunet
        model = "3d_fullres"
        folds = None
        trainer = "nnUNetTrainerV2_ep4000_nomirror"
        task_id = [253]

        self.setup_nnunet_c2c(model_dir)
        self.download_spine_model(model_dir)

        from totalsegmentator.nnunet import nnUNet_predict_image

        with nostdout():
            img, seg = nnUNet_predict_image(
                input_path,
                output_path,
                task_id,
                model=model,
                folds=folds,
                trainer=trainer,
                tta=False,
                multilabel_image=True,
                resample=None,
                crop=None,
                crop_path=None,
                task_name="total",
                nora_tag="None",
                preview=False,
                nr_threads_resampling=1,
                nr_threads_saving=6,
                quiet=False,
                verbose=False,
                test=0,
            )
        end = time()

        # Log total time for spine segmentation
        print(f"Total time for spine segmentation: {end-st:.2f}s.")

        seg_data = seg.get_fdata()
        seg = nib.Nifti1Image(seg_data, seg.affine, seg.header)

        return seg, img


class AortaDiameter(InferenceClass):
    def __init__(self):
        super().__init__()

    def normalize_img(self, img: np.ndarray) -> np.ndarray:
        """Normalize the image.
        Args:
            img (np.ndarray): Input image.
        Returns:
            np.ndarray: Normalized image.
        """
        return (img - img.min()) / (img.max() - img.min())

    def __call__(self, inference_pipeline):
        axial_masks = inference_pipeline.axial_masks  # list of 2D numpy arrays of shape (512, 512)
        ct_img = inference_pipeline.ct_image  # 3D numpy array of shape (512, 512, num_axial_slices)

        # image output directory
        output_dir = inference_pipeline.output_dir
        output_dir_slices = os.path.join(output_dir, "images/slices/")
        if not os.path.exists(output_dir_slices):
            os.makedirs(output_dir_slices)

        output_dir_summary = os.path.join(output_dir, "images/summary/")
        if not os.path.exists(output_dir_summary):
            os.makedirs(output_dir_summary)

        # Load CSV from Spine Pipeline
        csv_path = os.path.join(output_dir, "csv", "volume_lengths.csv")
        volume_df = pd.read_csv(csv_path)
        total_slices = volume_df['Length Before Cropping'].iloc[0]
        upper_level_index = volume_df['Upper Level Index'].iloc[0]
        lower_level_index = volume_df['Lower Level Index'].iloc[0]
        start_slice = total_slices - upper_level_index
        end_slice = total_slices - lower_level_index

        start_slice = end_slice

        DICOM_PATH = inference_pipeline.dicom_series_path
        dicom = pydicom.dcmread(DICOM_PATH + "/" + os.listdir(DICOM_PATH)[0])

        dicom.PhotometricInterpretation = "YBR_FULL"
        pixel_conversion = dicom.PixelSpacing
        print("Pixel conversion: " + str(pixel_conversion))
        RATIO_PIXEL_TO_MM = pixel_conversion[0]

        SLICE_COUNT = dicom["InstanceNumber"].value
        print(SLICE_COUNT)

        diameterDict = {}

        for i in range(len(ct_img)):
            
            print("CT IMAGE LENGTH")
            print(len(ct_img))

            print("SEG LENGTH")
            print(len(axial_masks))

            mask = axial_masks[i].astype("uint8")
            img = ct_img[i]

            img = np.clip(img, -300, 1800)
            img = self.normalize_img(img) * 255.0
            img = img.reshape((img.shape[0], img.shape[1], 1))
            img = np.tile(img, (1, 1, 3))

            if np.any(mask):  # If there is a segmentation
                contours, _ = cv2.findContours(mask, cv2.RETR_LIST, cv2.CHAIN_APPROX_NONE)

                if len(contours) != 0:
                    areas = [cv2.contourArea(c) for c in contours]
                    sorted_areas = np.sort(areas)
                    contours = contours[areas.index(sorted_areas[-1])]

                    if len(contours) >= 5:  # Check if there are enough points to fit an ellipse
                        img_copy = img.copy()

                        back = img_copy.copy()
                        cv2.drawContours(back, [contours], 0, (0, 255, 0), -1)

                        alpha = 0.25
                        img_copy = cv2.addWeighted(img_copy, 1 - alpha, back, alpha, 0)

                        ellipse = cv2.fitEllipse(contours)
                        (xc, yc), (d1, d2), angle = ellipse

                        cv2.ellipse(img_copy, ellipse, (0, 255, 0), 1)

                        xc, yc = ellipse[0]
                        cv2.circle(img_copy, (int(xc), int(yc)), 5, (0, 0, 255), -1)

                        rmajor = max(d1, d2) / 2
                        rminor = min(d1, d2) / 2

                        # Draw major axes
                        if angle > 90:
                            angle = angle - 90
                        else:
                            angle = angle + 90
                        xtop = xc + math.cos(math.radians(angle)) * rmajor
                        ytop = yc + math.sin(math.radians(angle)) * rmajor
                        xbot = xc + math.cos(math.radians(angle + 180)) * rmajor
                        ybot = yc + math.sin(math.radians(angle + 180)) * rmajor
                        cv2.line(img_copy, (int(xtop), int(ytop)), (int(xbot), int(ybot)), (0, 0, 255), 3)

                        # Draw minor axes
                        if angle > 90:
                            angle = angle - 90
                        else:
                            angle = angle + 90
                        x1 = xc + math.cos(math.radians(angle)) * rminor
                        y1 = yc + math.sin(math.radians(angle)) * rminor
                        x2 = xc + math.cos(math.radians(angle + 180)) * rminor
                        y2 = yc + math.sin(math.radians(angle + 180)) * rminor
                        cv2.line(img_copy, (int(x1), int(y1)), (int(x2), int(y2)), (255, 0, 0), 3)

                        pixel_length = rminor * 2
                        print("Pixel_length_minor: " + str(pixel_length))

                        area_px = cv2.contourArea(contours)
                        area_mm = round(area_px * RATIO_PIXEL_TO_MM)
                        area_cm = area_mm / 10

                        # diameter_mm = round((pixel_length) * RATIO_PIXEL_TO_MM)
                        diameter_mm = (pixel_length) * RATIO_PIXEL_TO_MM
                        diameter_cm = diameter_mm / 10

                        slice_number = start_slice - i
                        diameterDict[slice_number] = diameter_cm

                        img_copy = cv2.rotate(img_copy, cv2.ROTATE_90_COUNTERCLOCKWISE)

                        h, w, c = img_copy.shape
                        lbls = [
                            "Area (mm): " + str(area_mm) + "mm",
                            "Area (cm): " + str(area_cm) + "cm",
                            "Diameter (mm): " + str(diameter_mm) + "mm",
                            "Diameter (cm): " + str(diameter_cm) + "cm",
                            "Slice: " + str(slice_number),
                        ]
                        font = cv2.FONT_HERSHEY_SIMPLEX

                        scale = 0.03
                        fontScale = min(w, h) / (25 / scale)

                        cv2.putText(img_copy, lbls[0], (10, 40), font, fontScale, (0, 255, 0), 2)
                        cv2.putText(img_copy, lbls[1], (10, 70), font, fontScale, (0, 255, 0), 2)
                        cv2.putText(img_copy, lbls[2], (10, 100), font, fontScale, (0, 255, 0), 2)
                        cv2.putText(img_copy, lbls[3], (10, 130), font, fontScale, (0, 255, 0), 2)
                        cv2.putText(img_copy, lbls[4], (10, 160), font, fontScale, (0, 255, 0), 2)

                        cv2.imwrite(output_dir_slices + "slice" + str(slice_number) + ".png", img_copy)
            else:  # No segmentation
                diameterDict[start_slice - i] = 0

                img = cv2.rotate(img, cv2.ROTATE_90_COUNTERCLOCKWISE)

                h, w, c = img.shape
                lbls = [
                    "Area (mm): 0mm",
                    "Area (cm): 0cm",
                    "Diameter (mm): 0mm",
                    "Diameter (cm): 0cm",
                    "Slice: " + str(start_slice - i),
                ]
                font = cv2.FONT_HERSHEY_SIMPLEX

                scale = 0.03
                fontScale = min(w, h) / (25 / scale)

                cv2.putText(img, lbls[0], (10, 40), font, fontScale, (0, 255, 0), 2)
                cv2.putText(img, lbls[1], (10, 70), font, fontScale, (0, 255, 0), 2)
                cv2.putText(img, lbls[2], (10, 100), font, fontScale, (0, 255, 0), 2)
                cv2.putText(img, lbls[3], (10, 130), font, fontScale, (0, 255, 0), 2)
                cv2.putText(img, lbls[4], (10, 160), font, fontScale, (0, 255, 0), 2)

                cv2.imwrite(output_dir_slices + "slice" + str(start_slice - i) + ".png", img)

        
        # for i in range(l3_masks):
        #     print("analyzing l3_masks")
            
            

        #     ## max transerverse on L3
        #     l3_diameter = 0 
            
        #     ## max aortic diameter (prev. computed)
        #     max_diameter = inference_pipeline.max_diameter

        #     ## a4_v index calculation
        #     a4_v = inference_pipeline.max_diameter / l3_diameter

        #     print(a4_v)


        # Create and save the diameter progression graph
        # plt.bar(list(diameterDict.keys()), diameterDict.values(), color="b")
        # plt.title(r"$\bf{Diameter}$" + " " + r"$\bf{Progression}$")
        # plt.xlabel("Slice Number")
        # plt.ylabel("Diameter Measurement (cm)")
        # plt.savefig(output_dir_summary + "diameter_graph.png", dpi=500)

        print(diameterDict)
        max_diameter_slice = max(diameterDict.items(), key=lambda x: x[1])[0]
        max_diameter_value = diameterDict[max_diameter_slice]

        print("Max diameter slice:", max_diameter_slice)
        print("Max diameter value:", max_diameter_value)

        inference_pipeline.max_diameter = max_diameter_value
        inference_pipeline.max_diameter_slice = max_diameter_slice
        img = ct_img[max_diameter_slice - start_slice]
        img = np.clip(img, -300, 1800)
        img = self.normalize_img(img) * 255.0
        img = img.reshape((img.shape[0], img.shape[1], 1))
        img2 = np.tile(img, (1, 1, 3))
        img2 = cv2.rotate(img2, cv2.ROTATE_90_COUNTERCLOCKWISE)

        img1 = cv2.imread(output_dir_slices + "slice" + str(max_diameter_slice) + ".png")

        border_size = 3
        img1 = cv2.copyMakeBorder(
            img1,
            top=border_size,
            bottom=border_size,
            left=border_size,
            right=border_size,
            borderType=cv2.BORDER_CONSTANT,
            value=[0, 244, 0],
        )
        # img2 = cv2.copyMakeBorder(
        #     img2,
        #     top=border_size,
        #     bottom=border_size,
        #     left=border_size,
        #     right=border_size,
        #     borderType=cv2.BORDER_CONSTANT,
        #     value=[244, 0, 0],
        # )

        # vis = np.concatenate((img2, img1), axis=1)
        cv2.imwrite(output_dir_summary + "out.png", img1)

        # Create a video from the image slices
        # # Create a video from the image slices
        image_folder = output_dir_slices
        fps = 20

        # Function to extract the number from the filename
        def extract_number(filename):
            match = re.search(r'(\d+)', filename)
            return int(match.group(1)) if match else float('inf')

        # Get and sort the image files
        image_files = [
            os.path.join(image_folder, img)
            for img in sorted(os.listdir(image_folder), key=extract_number)
            if img.endswith(".png")
        ]

        # Create the video clip
        clip = ImageSequenceClip(image_files, fps=fps)
        clip.write_videofile(os.path.join(output_dir_summary, "aaa.mp4"))

        return {}

    # def __init__(self):
    #     super().__init__()

    # def normalize_img(self, img: np.ndarray) -> np.ndarray:
    #     """Normalize the image.
    #     Args:
    #         img (np.ndarray): Input image.
    #     Returns:
    #         np.ndarray: Normalized image.
    #     """
    #     return (img - img.min()) / (img.max() - img.min())

    # def __call__(self, inference_pipeline):
        axial_masks = inference_pipeline.axial_masks  # list of 2D numpy arrays of shape (512, 512)
        ct_img = inference_pipeline.ct_image  # 3D numpy array of shape (512, 512, num_axial_slices)

        # image output directory
        output_dir = inference_pipeline.output_dir
        output_dir_slices = os.path.join(output_dir, "images/slices/")
        if not os.path.exists(output_dir_slices):
            os.makedirs(output_dir_slices)

        output_dir_summary = os.path.join(output_dir, "images/summary/")
        if not os.path.exists(output_dir_summary):
            os.makedirs(output_dir_summary)

        # Load CSV from Spine Pipeline
        csv_path = os.path.join(output_dir, "csv", "volume_lengths.csv")
        volume_df = pd.read_csv(csv_path)
        total_slices = volume_df['Length Before Cropping'].iloc[0]
        upper_level_index = volume_df['Upper Level Index'].iloc[0]
        lower_level_index = volume_df['Lower Level Index'].iloc[0]
        start_slice = total_slices - upper_level_index
        end_slice = total_slices - lower_level_index

        start_slice = end_slice

        DICOM_PATH = inference_pipeline.dicom_series_path
        dicom = pydicom.dcmread(DICOM_PATH + "/" + os.listdir(DICOM_PATH)[0])

        dicom.PhotometricInterpretation = "YBR_FULL"
        pixel_conversion = dicom.PixelSpacing
        print("Pixel conversion: " + str(pixel_conversion))
        RATIO_PIXEL_TO_MM = pixel_conversion[0]

        SLICE_COUNT = dicom["InstanceNumber"].value
        print(SLICE_COUNT)

        diameterDict = {}

        for i in range(len(ct_img)):
            mask = axial_masks[i].astype("uint8")
            img = ct_img[i]

            img = np.clip(img, -300, 1800)
            img = self.normalize_img(img) * 255.0
            img = img.reshape((img.shape[0], img.shape[1], 1))
            img = np.tile(img, (1, 1, 3))

            if np.any(mask):  # If there is a segmentation
                contours, _ = cv2.findContours(mask, cv2.RETR_LIST, cv2.CHAIN_APPROX_NONE)

                if len(contours) != 0:
                    areas = [cv2.contourArea(c) for c in contours]
                    sorted_areas = np.sort(areas)
                    contours = contours[areas.index(sorted_areas[-1])]

                    if len(contours) >= 5:  # Check if there are enough points to fit an ellipse
                        img_copy = img.copy()

                        back = img_copy.copy()
                        cv2.drawContours(back, [contours], 0, (0, 255, 0), -1)

                        alpha = 0.25
                        img_copy = cv2.addWeighted(img_copy, 1 - alpha, back, alpha, 0)

                        ellipse = cv2.fitEllipse(contours)
                        (xc, yc), (d1, d2), angle = ellipse

                        cv2.ellipse(img_copy, ellipse, (0, 255, 0), 1)

                        xc, yc = ellipse[0]
                        cv2.circle(img_copy, (int(xc), int(yc)), 5, (0, 0, 255), -1)

                        rmajor = max(d1, d2) / 2
                        rminor = min(d1, d2) / 2

                        # Draw major axes
                        if angle > 90:
                            angle = angle - 90
                        else:
                            angle = angle + 90
                        xtop = xc + math.cos(math.radians(angle)) * rmajor
                        ytop = yc + math.sin(math.radians(angle)) * rmajor
                        xbot = xc + math.cos(math.radians(angle + 180)) * rmajor
                        ybot = yc + math.sin(math.radians(angle + 180)) * rmajor
                        cv2.line(img_copy, (int(xtop), int(ytop)), (int(xbot), int(ybot)), (0, 0, 255), 3)

                        # Draw minor axes
                        if angle > 90:
                            angle = angle - 90
                        else:
                            angle = angle + 90
                        x1 = xc + math.cos(math.radians(angle)) * rminor
                        y1 = yc + math.sin(math.radians(angle)) * rminor
                        x2 = xc + math.cos(math.radians(angle + 180)) * rminor
                        y2 = yc + math.sin(math.radians(angle + 180)) * rminor
                        cv2.line(img_copy, (int(x1), int(y1)), (int(x2), int(y2)), (255, 0, 0), 3)

                        pixel_length = rminor * 2
                        print("Pixel_length_minor: " + str(pixel_length))

                        area_px = cv2.contourArea(contours)
                        area_mm = round(area_px * RATIO_PIXEL_TO_MM)
                        area_cm = area_mm / 10

                        diameter_mm = (pixel_length) * RATIO_PIXEL_TO_MM
                        diameter_cm = diameter_mm / 10

                        slice_number = start_slice - i
                        diameterDict[slice_number] = diameter_cm

                        img_copy = cv2.rotate(img_copy, cv2.ROTATE_90_COUNTERCLOCKWISE)

                        h, w, c = img_copy.shape
                        lbls = [
                            "Area (mm): " + str(area_mm) + "mm",
                            "Area (cm): " + str(area_cm) + "cm",
                            "Diameter (mm): " + str(diameter_mm) + "mm",
                            "Diameter (cm): " + str(diameter_cm) + "cm",
                            "Slice: " + str(slice_number),
                        ]
                        font = cv2.FONT_HERSHEY_SIMPLEX

                        scale = 0.03
                        fontScale = min(w, h) / (25 / scale)

                        cv2.putText(img_copy, lbls[0], (10, 40), font, fontScale, (0, 255, 0), 2)
                        cv2.putText(img_copy, lbls[1], (10, 70), font, fontScale, (0, 255, 0), 2)
                        cv2.putText(img_copy, lbls[2], (10, 100), font, fontScale, (0, 255, 0), 2)
                        cv2.putText(img_copy, lbls[3], (10, 130), font, fontScale, (0, 255, 0), 2)
                        cv2.putText(img_copy, lbls[4], (10, 160), font, fontScale, (0, 255, 0), 2)

                        cv2.imwrite(output_dir_slices + "slice" + str(slice_number) + ".png", img_copy)
            else:  # No segmentation
                diameterDict[start_slice - i] = 0

                img = cv2.rotate(img, cv2.ROTATE_90_COUNTERCLOCKWISE)

                h, w, c = img.shape
                lbls = [
                    "Area (mm): 0mm",
                    "Area (cm): 0cm",
                    "Diameter (mm): 0mm",
                    "Diameter (cm): 0cm",
                    "Slice: " + str(start_slice - i),
                ]
                font = cv2.FONT_HERSHEY_SIMPLEX

                scale = 0.03
                fontScale = min(w, h) / (25 / scale)

                cv2.putText(img, lbls[0], (10, 40), font, fontScale, (0, 255, 0), 2)
                cv2.putText(img, lbls[1], (10, 70), font, fontScale, (0, 255, 0), 2)
                cv2.putText(img, lbls[2], (10, 100), font, fontScale, (0, 255, 0), 2)
                cv2.putText(img, lbls[3], (10, 130), font, fontScale, (0, 255, 0), 2)
                cv2.putText(img, lbls[4], (10, 160), font, fontScale, (0, 255, 0), 2)

                cv2.imwrite(output_dir_slices + "slice" + str(start_slice - i) + ".png", img)

        # Create and save the diameter progression graph
        plt.bar(list(diameterDict.keys()), diameterDict.values(), color="b")
        plt.title(r"$\bf{Diameter}$" + " " + r"$\bf{Progression}$")
        plt.xlabel("Slice Number")
        plt.ylabel("Diameter Measurement (cm)")
        plt.savefig(output_dir_summary + "diameter_graph.png", dpi=500)

        print(diameterDict)
        max_diameter_slice = max(diameterDict.items(), key=lambda x: x[1])[0]
        max_diameter_value = diameterDict[max_diameter_slice]

        print("Max diameter slice:", max_diameter_slice)
        print("Max diameter value:", max_diameter_value)

        inference_pipeline.max_diameter = max_diameter_value
        inference_pipeline.max_diameter_slice = max_diameter_slice
        # img = ct_img[max_diameter_slice]
        # img = np.clip(img, -300, 1800)
        # img = self.normalize_img(img) * 255.0
        # img = img.reshape((img.shape[0], img.shape[1], 1))
        # img2 = np.tile(img, (1, 1, 3))
        # img2 = cv2.rotate(img2, cv2.ROTATE_90_COUNTERCLOCKWISE)

        img1 = cv2.imread(output_dir_slices + "slice" + str(max_diameter_slice) + ".png")

        border_size = 3
        img1 = cv2.copyMakeBorder(
            img1,
            top=border_size,
            bottom=border_size,
            left=border_size,
            right=border_size,
            borderType=cv2.BORDER_CONSTANT,
            value=[0, 244, 0],
        )
        # img2 = cv2.copyMakeBorder(
        #     img2,
        #     top=border_size,
        #     bottom=border_size,
        #     left=border_size,
        #     right=border_size,
        #     borderType=cv2.BORDER_CONSTANT,
        #     value=[244, 0, 0],
        # )

        # vis = np.concatenate((img2, img1), axis=1)
        cv2.imwrite(output_dir_summary + "out.png", img1)

        # # Create a video from the image slices
        image_folder = output_dir_slices
        fps = 20

        # Function to extract the number from the filename
        def extract_number(filename):
            match = re.search(r'(\d+)', filename)
            return int(match.group(1)) if match else float('inf')

        # Get and sort the image files
        image_files = [
            os.path.join(image_folder, img)
            for img in sorted(os.listdir(image_folder), key=extract_number)
            if img.endswith(".png")
        ]

        # Create the video clip
        clip = ImageSequenceClip(image_files, fps=fps)
        clip.write_videofile(os.path.join(output_dir_summary, "aaa.mp4"))

        return {}

class AortaMetricsSaver(InferenceClass):
    """Save metrics to a CSV file."""

    def __init__(self):
        super().__init__()

    def __call__(self, inference_pipeline):
        """Save metrics to a CSV file."""
        self.max_diameter = inference_pipeline.max_diameter
        self.max_diameter_slice = inference_pipeline.max_diameter_slice
        self.dicom_series_path = inference_pipeline.dicom_series_path
        self.output_dir = inference_pipeline.output_dir
        self.csv_output_dir = os.path.join(self.output_dir, "metrics")
        if not os.path.exists(self.csv_output_dir):
            os.makedirs(self.csv_output_dir, exist_ok=True)
        self.save_results()
        return {}

    def save_results(self):
        """Save results to a CSV file."""
        _, filename = os.path.split(self.dicom_series_path)
        data = [[filename, str(self.max_diameter), str(self.max_diameter_slice)]]
        df = pd.DataFrame(data, columns=["Filename", "Max Diameter", "Max Diameter Slice"])
        df.to_csv(os.path.join(self.csv_output_dir, "aorta_metrics.csv"), index=False)
        # script_path = "/scratch/users/adritrao/Comp2Comp/run_nnunet.sh"
        # print("resetting nnunet")
        # template_content = """#!/bin/bash
        # #SBATCH --job-name=test_job
        # #SBATCH --output=test_job.%j.out
        # #SBATCH --error=test_job.%j.err
        # #SBATCH --ntasks=1
        # #SBATCH --cpus-per-task=1
        # #SBATCH --mem-per-cpu=8G
        # #SBATCH -p gpu
        # #SBATCH --gpus=1
        # #SBATCH --time=00:10:00

        # source /scratch/users/adritrao/miniconda/etc/profile.d/conda.sh
        # conda init bash
        # conda deactivate
        # conda activate train
        # nnUNet_predict -i {INPUT_PATH} -o {OUTPUT_FOLDER} -t {TASK_ID} -m {MODEL} -f {FOLDS} -tr {TRAINER}
        # conda init
        # conda deactivate
        # conda init
        # conda activate aaa
        # """
        #     # Write the template content to the script file
        # with open(script_path, "w") as script_file:
        #     script_file.write(template_content)

        print("done resetting")
