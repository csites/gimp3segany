#!/home/chuck/venv/bin/python3
# -*- coding: utf-8 -*-
#
'''
Script to generate Meta Segment Anything masks.

Adapted from:
https://github.com/facebookresearch/segment-anything/blob/main/notebooks/predictor_example.ipynb
Original Author: Shrinivas Kulkarni
Adapted by: Chuck Sites

This program is free software; you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation; either version 3 of the License, or
(at your option) any later version.

This program is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU General Public License for more details.

You should have received a copy of the GNU General Public License along
with this program; if not, write to the Free Software Foundation, Inc.,
51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA.
'''

import torch
import numpy as np
import cv2
from segment_anything import sam_model_registry, SamAutomaticMaskGenerator, SamPredictor
import logging
import sys

class SegmentAnythingProcessor:
    def __init__(self, model_type, checkpoint_path):
        self.model_type = model_type
        self.checkpoint_path = checkpoint_path
        self.sam = sam_model_registry[model_type](checkpoint=checkpoint_path)
        if torch.cuda.is_available():
            self.sam.to(device='cuda')
            logging.info("SAM is running cuda")

    def pack_bool_array(self, filepath, arr):
        packed_data = bytearray()
        num_rows = len(arr)
        num_cols = len(arr[0])

        packed_data.extend([num_rows >> 24, (num_rows >> 16) & 255, (num_rows >> 8) & 255, num_rows & 255])
        packed_data.extend([num_cols >> 24, (num_cols >> 16) & 255, (num_cols >> 8) & 255, num_cols & 255])

        current_byte = 0
        bit_position = 0

        for row in arr:
            for boolean_value in row:
                if boolean_value:
                    current_byte |= (1 << bit_position)
                bit_position += 1

                if bit_position == 8:
                    packed_data.append(current_byte)
                    current_byte = 0
                    bit_position = 0

        if bit_position > 0:
            packed_data.append(current_byte)

        with open(filepath, 'wb') as f:
            f.write(packed_data)

        return packed_data

    def save_mask(self, filepath, mask_arr, format_binary):
        if format_binary:
            self.pack_bool_array(filepath, mask_arr)
        else:
            with open(filepath, 'w') as f:
                for row in mask_arr:
                    f.write(''.join(str(int(val)) for val in row) + '\n')

    def save_masks(self, masks, save_file_no_ext, format_binary):
        for i, mask in enumerate(masks):
            filepath = save_file_no_ext + str(i) + '.seg'
            arr = [[val for val in row] for row in mask]
            logging.info(f"Saving mask to: {filepath}")
            self.save_mask(filepath, arr, format_binary)

    def segment_auto(self, cv_image, save_file_no_ext, format_binary):
        mask_generator = SamAutomaticMaskGenerator(self.sam)
        masks = mask_generator.generate(cv_image)
        masks = [mask['segmentation'] for mask in masks]
        self.save_masks(masks, save_file_no_ext, format_binary)

    def segment_box(self, cv_image, mask_type, box_cos, save_file_no_ext, format_binary):
        predictor = SamPredictor(self.sam)
        predictor.set_image(cv_image)

        input_box = np.array(box_cos)
        masks, _, _ = predictor.predict(
            point_coords=None,
            point_labels=None,
            box=input_box,
            multimask_output=(mask_type == 'Multiple'),
        )
        self.save_masks(masks, save_file_no_ext, format_binary)

    def segment_sel(self, cv_image, mask_type, sel_file, box_cos, save_file_no_ext, format_binary):
        pts = []
        with open(sel_file, 'r') as f:
            lines = f.readlines()
            for line in lines:
                cos = line.split(' ')
                pts.append([int(cos[0]), int(cos[1])])

        predictor = SamPredictor(self.sam)
        predictor.set_image(cv_image)

        input_point = np.array(pts)
        input_label = np.array([1 for i in range(len(input_point))])

        if box_cos is None:
            masks, scores, logits = predictor.predict(
                point_coords=input_point,
                point_labels=input_label,
                multimask_output=(mask_type == 'Multiple'),
            )
        else:
            input_box = np.array(box_cos)
            masks, scores, logits = predictor.predict(
                point_coords=input_point,
                point_labels=input_label,
                box=input_box,
                multimask_output=(mask_type == 'Multiple'),
            )
        self.save_masks(masks, save_file_no_ext, format_binary)

    def run_segmentation(self, ip_file, seg_type, mask_type, save_file_no_ext, format_binary, sel_file=None, box_cos=None):
        cv_image = cv2.imread(ip_file)
        cv_image = cv2.cvtColor(cv_image, cv2.COLOR_BGR2RGB)

        if seg_type == 'Auto':
            logging.info("segment Auto")
            self.segment_auto(cv_image, save_file_no_ext, format_binary)
        elif seg_type in {'Selection', 'Box-Selection'}:
            logging.info("segment Selection")
            self.segment_sel(cv_image, mask_type, sel_file, box_cos, save_file_no_ext, format_binary)
        elif seg_type == 'Box':
            logging.info("segment Box")
            self.segment_box(cv_image, mask_type, box_cos, save_file_no_ext, format_binary)
        else:
            raise ValueError(f"Unknown segmentation type: {seg_type}")
        logging.info("seganybridge.py is complete!")
        
