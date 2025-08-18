#!/home/chuck/venv/bin/python3
# -*- coding: utf-8 -*-
#
#   GIMP plugin for integration of with the Segment Anything
#   Original work: Copyright (C) 2023  Shrinivas Kulkarni
#   Port to Gimp Version 3.0: Copyright (C) 2025 Chuck Sites
#
#   GIMP: Copyright (C) 1995 Spencer Kimball and Peter Mattis
#
#   gimp-segany-plug-in.py
#   A plug-in to add the Segment Anything Layer plug-in
#
#   This program is free software: you can redistribute it and/or modify
#   it under the terms of the GNU General Public License as published by
#   the Free Software Foundation; either version 3 of the License, or
#   (at your option) any later version.
#
#   This program is distributed in the hope that it will be useful,
#   but WITHOUT ANY WARRANTY; without even the implied warranty of
#   MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#   GNU General Public License for more details.
#
#   You should have received a copy of the GNU General Public License
#   along with this program.  If not, see <https://www.gnu.org/licenses/>.

import os
import sys

import gi
gi.require_version('Gimp', '3.0')
from gi.repository import Gimp
gi.require_version('GimpUi', '3.0')
from gi.repository import GimpUi
from gi.repository import GLib
from gi.repository import Gegl
from gi.repository import GObject
from gi.repository import Gio
gi.require_version('Gtk', '3.0')
from gi.repository import Gtk 
gi.require_version('Gdk', '3.0')
from gi.repository import Gdk

import tempfile
import subprocess
import threading
from os.path import exists
from array import array
import random
import glob
import struct
import json
import logging
import functools
import chardet
import traceback
import cv2
from seganybridge import SegmentAnythingProcessor

# Not used currently (plugin pnly works with python2)
def getVersion():
    sys.version_info[0]


def configLogging(level):
    logging.basicConfig(level=level,
                        format='%(asctime)s - %(levelname)s - %(message)s',
                        datefmt='%Y-%m-%d %H:%M:%S')

# Plugin Error Handler
# Safe way to handle errors with GIMP 
def return_plugin_error(procedure, error_message):
    try:
        gerror = GLib.Error.new_literal(GLib.quark_from_static_string("PLUGIN-ERROR"), 0, error_message)
        return procedure.new_return_values(Gimp.PDBStatusType.EXECUTION_ERROR, gerror.message) # Changed line
    except UnicodeDecodeError:
        # Handle encoding error, replace problematic characters
        safe_message = error_message.encode('utf-8', 'replace').decode('utf-8')
        gerror = GLib.Error.new_literal(GLib.quark_from_static_string("PLUGIN-ERROR"), 0, safe_message)
        return procedure.new_return_values(Gimp.PDBStatusType.EXECUTION_ERROR, gerror.message) # Changed line
    except Exception as e:
        print(f"Error creating plugin error: {e}")
        return procedure.new_return_values(Gimp.PDBStatusType.EXECUTION_ERROR, str(e)) # Added line

# Helper callback function
def getPathDict(image):
    path_collection = image.get_paths()
    print(type(path_collection))  # Add this line
    pathDict = {}

    def add_path_to_dict(path):
        pathDict[path.name] = path

    for path in path_collection:
        add_path_to_dict(path)

    return pathDict

# run_subprocess function.   This is to test the use of threads to launch a sub_process
# That will be the following;
# thread = threading.Thread(target=run_subprocess, args=(cmd,))
# thread.start()
# thread.join()
def run_subprocess(cmd):
    child = subprocess.Popen(cmd, shell=False, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    stdout, stderr = child.communicate()
    print(stdout)

# Read a binary file containing a packed boolean array (a 2D array of True/False values) and
# convert it into a standard Python list of lists where each inner list represents a row of the boolean arra
def unpackBoolArray(filepath):
    with open(filepath, 'rb') as file:
        packed_data = file.read()

    num_rows, num_cols = struct.unpack(">II", packed_data[:8])
     
    unpacked_data = []
    byte_index = 8       # Skip the first 8 bytes for num_rows and num_cols
    bit_position = 0
    
    for _ in range(num_rows):
        unpacked_row = []
        for _ in range(num_cols):
            current_byte = packed_data[byte_index]
            boolean_value = (current_byte >> bit_position) & 1
            unpacked_row.append(boolean_value)

            bit_position += 1
            if bit_position == 8:
                bit_position = 0
                byte_index += 1 # Increment only when needed

        unpacked_data.append(unpacked_row)

    return unpacked_data

def readMaskFile(filepath, formatBinary):
    print("readMaskFile: ",filepath)
    if formatBinary:
        return unpackBoolArray(filepath)
    else:  # Only for testing
        mask = []
        try: # Add try/except block for robustness
            with open(filepath, 'r') as f:
                for line in f: # Iterate through lines directly
                    mask.append([val == '1' for val in line.strip().split()]) # Split and strip the line
        except FileNotFoundError:
            logging.error(f"Mask file not found: {filepath}")
            return None # Return None to indicate failure
        except Exception as e:
            logging.error(f"Error reading mask file: {str(e)}")
            return None
        return mask

def exportSelection(image, expfile, exportCnt):
    selection_bounds_tuple = Gimp.Selection.bounds(image)

    if not selection_bounds_tuple.non_empty:  # Check if selection is empty
        logging.warning("No selection found. Exporting no points.")
        try:  # Try/except block for file operations even if no selection
            with open(expfile, 'w') as f:
                return True  # Successfully created (empty) file
        except Exception as e:
            logging.error(f"Error creating export file: {str(e)}")
            return None

    try:  # Try/except block encompassing both calculation and writing
        x1, y1, x2, y2 = selection_bounds_tuple.x1, selection_bounds_tuple.y1, selection_bounds_tuple.x2, selection_bounds_tuple.y2
        coords = []
        numPts = (x2 - x1) * (y2 - y1)
        if exportCnt >= numPts:
            selIdxs = range(numPts)
        else:
            selIdxs = random.sample(range(numPts), exportCnt)

        for selIdx in selIdxs:
            x = x1 + selIdx % (x2 - x1)
            y = y1 + int(selIdx / (x2 - x1))
            value = Gimp.Selection.value(image, x, y)
            if value > 200:
                coords.append((x, y))

        with open(expfile, 'w') as f:
            for co in coords:
                f.write(f"{co[0]} {co[1]}\n")

        return True  # Indicate successful export

    except Exception as e:
        logging.error(f"Error during selection export: {str(e)}")
        return None  # Indicate failure
    
# Clean up.
def getRandomColor(layerCnt):
    uniqueColors = set()
    while len(uniqueColors) < layerCnt:
        color = (random.randint(0, 255), random.randint(0, 255), random.randint(0, 255))
        uniqueColors.add(color)  # set.add() is efficient, avoids the if check

    return list(uniqueColors)

# Change for Gimp 3.0 native.  Create corresponding layers in the GIMP image, visualizing the segmented regions.
def createLayers(image, maskFileNoExt, userSelColor, formatBinary):
    try:
        logging.info("In createLayers (try)")
        # ... rest of your code ...

        width = image.get_width()
        height = image.get_height()
        logging.info(f"In createLayers:  {maskFileNoExt}")
        idx = 0
        maxLayers = 99999

        parent = Gimp.LayerGroup.new(image)  # Correct way to create layer group
        image.insert_layer(parent, None, 0)  # Use image.insert_layer
        parent.set_opacity(50)

        uniqueColors = getRandomColor(layerCnt=999)

        if image.get_base_type() == Gimp.ImageType.GRAY:  # Use Gimp enum
            layerType = Gimp.ImageType.GRAYA
            userSelColor = [100, 255]
        else:
            layerType = Gimp.ImageType.RGBA
            logging.info(f"createLayers: {width},{height} {maskFileNoExt}")

        while idx < maxLayers:
            filepath = maskFileNoExt + str(idx) + '.seg'
            logging.debug(f"Layer source filepath: {filepath}")
            if exists(filepath):
                logging.info(f"Creating Layer: {(idx + 1)}")
                newlayer = Gimp.Layer.new(image, f"Segment Auto {idx}",  # Correct way to create layer
                                          layerType, 100,
                                          Gimp.LayerMode.NORMAL)  # Use Gimp enum
                image.insert_layer(newlayer, parent, 0)  # Use image.insert_layer and parent
                newlayer.set_visible(False)  # Use set_visible
                buffer = newlayer.get_buffer()

                maskVals = readMaskFile(filepath, formatBinary)
                maskColor = userSelColor if userSelColor is not None else list(uniqueColors[idx]) + [255]
            
            #     --- FIX: Populate a single bytearray instead of a list of strings
                pixels = bytearray(width * height * pix_size)
                for x, row in enumerate(maskVals):
                    for y, p in enumerate(row):
                        if p:
                            pos = (y + width * x) * pix_size
                            pixels[pos: pos + pix_size] = mask_color_bytes
            
                buffer.set(rect, babl_format, bytes(pixels))
                # --- END FIX

                x = 0
                for line in maskVals:
                    y = 0
                    for p in line:
                        if p:
                            # Set pixel in buffer
                            buffer.set_pixel(x, y, maskColor)
                        else:
                            buffer.set_pixel(x, y, [0, 0, 0, 0] if layerType == Gimp.ImageType.RGBA else [0, 0])
                            y += 1
                            x += 1

                newlayer.flush()
                newlayer.merge_shadow(True)
                newlayer.update(0, 0, width, height)

            else:
                break

        return idx
    except Exception as e:
        logging.error(f"Error in createLayers: {e}")
        logging.error(traceback.format_exc())
        return 0
    
def cleanup(filepathPrefix):
    for f in glob.glob(filepathPrefix + '*'):
        try:  # Add try/except for robust file removal
            os.remove(f)
        except OSError as e:
            logging.error(f"Error removing file {f}: {str(e)}")

    
def getBoxCos(image, boxPathDict, pathName):
    path = boxPathDict.get(pathName)
    if path is None:
        logging.error('Error: Please create a box path and select it')
        return None

    try:  # Handle potential errors with path access
        strokes = path.strokes  # Access strokes
        if not strokes: # Check for empty strokes
            logging.error('Error: Path has no strokes.')
            return None

        points = strokes[0].points[0]  # Access points
        ptsCnt = len(points)

        if ptsCnt != 24:
            logging.error(f'Error: Path is not a box! {ptsCnt}')
            return None

        topLeft = [points[2], points[3]]
        bottomRight = [points[14], points[15]]
        return topLeft + bottomRight
    except (AttributeError, IndexError) as e: # Catch potential errors
        logging.error(f"Error accessing path data: {str(e)}")
        return None
    
class DialogValue:
    def __init__(self, filepath):
        data = None
        self.filepath = filepath
        self.pythonPath = None
        self.modelType = 'vit_h'
        self.checkPtPath = None
        self.maskType = 'Multiple'
        self.segType = 'Auto'
        self.isRandomColor = False
        self.maskColor = [255, 0, 0, 255]
        self.selPtCnt = 10
        self.selBoxPathName = None
        self.formatBinary = False
        
        try:
            with open(self.filepath, 'r') as f:
                data = json.load(f)
                self.pythonPath = data.get('pythonPath', self.pythonPath)
                self.modelType = data.get('modelType', self.modelType)
                self.checkPtPath = data.get('checkPtPath', self.checkPtPath)
                self.maskType = data.get('maskType', self.maskType)
                self.segType = data.get('segType', self.segType)
                self.isRandomColor = data.get('isRandomColor', self.isRandomColor)
                self.maskColor = data.get('maskColor', self.maskColor)
                self.selPtCnt = data.get('selPtCnt', self.selPtCnt)
                self.selBoxPathName = data.get('selBoxPathName', self.selBoxPathName) # Added this
                self.formatBinary = data.get('formatBinary', self.formatBinary) # Add this too.
        except FileNotFoundError:
            logging.info(f"Configuration file not found: {self.filepath}")
        except json.JSONDecodeError as e:
            logging.info(f"Error decoding JSON in {self.filepath}: {str(e)}")
        except Exception as e:
            logging.info('Error reading json : %s' % e)

    def persist(self):
        data = self.__dict__.copy()

        mask_color = data.get('maskColor')
        if isinstance(mask_color, Gdk.RGBA):  # Use Gdk.RGBA
            data['maskColor'] = [int(mask_color.red * 255), int(mask_color.green * 255), int(mask_color.blue * 255), int(mask_color.alpha * 255)] # Convert to int
        data['formatBinary'] = self.formatBinary    
        try:
            with open(self.filepath, 'w') as f:
                json.dump(data, f)
        except Exception as e:
            logging.error(f"Error persisting settings: {str(e)}")

    def load_settings(self):
        try:
            with open(self.filepath, 'r') as f:
                settings = json.load(f)

            mask_color_list = settings.get("maskColor")  # Get the list from JSON
            if mask_color_list:
                mask_color = Gdk.RGBA()  # Use Gdk.RGBA
                mask_color.red = mask_color_list[0] / 255
                mask_color.green = mask_color_list[1] / 255
                mask_color.blue = mask_color_list[2] / 255
                mask_color.alpha = mask_color_list[3] / 255
                self.maskColor = mask_color
            else:
                mask_color = Gdk.RGBA()
                mask_color.red = 1
                mask_color.green = 0
                mask_color.blue = 0
                mask_color.alpha = 1
                self.maskColor = mask_color

        except (FileNotFoundError, json.JSONDecodeError):
            # Handle errors
            pass            

# Gimp 3.0 change to Gtk. CBS:
def showError(message):
    dialog = Gtk.MessageDialog(
        text=message,  # Use 'text' instead of positional argument
        modal=True,  # Use modal=True
        destroy_with_parent=True, # Use destroy_with_parent=True
        message_type=Gtk.MessageType.ERROR, # Use Gtk.MessageType enum
        buttons=Gtk.ButtonsType.OK # Use Gtk.ButtonsType enum
    )

    dialog.run()
    dialog.destroy()


def kepPressNum(widget, event):
    allowedKeys = set([Gtk.KEY_Home, Gtk.KEY_End, Gtk.KEY_Left,
                       Gtk.KEY_Right, Gtk.KEY_Delete,
                       Gtk.KEY_BackSpace])
    keyval = event.get_keyval() # Use get_keyval()

    if (keyval < Gtk.KEY_0 or keyval > Gtk.KEY_9) and keyval not in allowedKeys: # Use Gtk.KEY_* enums
        return True  # Ignore the keypress
    return False  # Allow the keypress


def onRandomToggled(checkbox, controlsToHide):
    checked = checkbox.get_active()
    for control in controlsToHide:
        control.set_visible(not checked) # Use set_visible()

# Gimp 3 changes.
def getRightAlignLabel(labelStr):
    label = Gtk.Label(labelStr)
    alignment = Gtk.Alignment(xalign=1, yalign=0.5, xscale=0, yscale=0)
    alignment.add(label)
    return alignment

# Gimp 3 changes.
def validateOptions(image, values):
    print("validate_Values:", values)
    if not image:
        logging.error("No image provided.")
        return False
    if values.segType in {'Selection', 'Box-Selection', 'Box'}:
        try:
            selection_bounds_tuple = Gimp.Selection.bounds(image)
            print("Selection: ", selection_bounds_tuple)
            if not selection_bounds_tuple.non_empty: # Access by name
                selection_bounds = None
                print("No selection exists.")
            else:
                selection_bounds = (selection_bounds_tuple.x1, selection_bounds_tuple.y1,
                                    selection_bounds_tuple.x2, selection_bounds_tuple.y2) # Access by name
                print("x1,y1, x2,y2 =", selection_bounds)
        except Exception as e:
            print(f"Error getting selection bounds: {e}")
            selection_bounds = None
            return False

        if selection_bounds is None:
            showError('No Selection! For the Segmentation Types: Box, ' +
                      'Box-Selection and Selection to work you need ' +
                      'to select an area on the image')
            return False

    return True

# A little helper function
def getRightAlignLabel(text):
    label = Gtk.Label(label=text)
    # ... other code to set alignment ...
    return label  # Return the created label widget

# We use this to replace the plugin_main() -- CBS.
class SegAny(Gimp.PlugIn):  # Inherit from Gimp.PlugIn
       
    def do_query_procedures(self):
        return ["plug-in-segany-python"]  # Or a more descriptive name

    def do_set_i18n(self, name):
        return True, 'gimp30-python', None
#        return False  # No i18n support

    def do_create_procedure(self, name):
        procedure = Gimp.ImageProcedure.new(self, name,
                                            Gimp.PDBProcType.PLUGIN,
                                            self.run, None)

        procedure.set_image_types("RGB*, GRAY*")  # Or "*" for all image types
        procedure.set_menu_label("Segment Anything Mask Layers")
        procedure.add_menu_path("<Image>/Image/Segment Anything Layers...")
        procedure.set_documentation("Segment Anything", "Segment Anything", name)
        procedure.set_attribution("Ported By: Chuck Sites", "Original Code By: Shrinivas Kulkarni 2023", "2025")
        return procedure

    # Callback functions for file chooser dialogs
    def on_python_file_clicked(widget, dialog, values, file_button):
        file_chooser = Gtk.FileChooserDialog(
            title="Select Python Executable",
            parent=dialog,
            action=Gtk.FileChooserAction.OPEN
        )
        file_chooser.add_buttons(
            Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL,
            Gtk.STOCK_OPEN, Gtk.ResponseType.OK
        )

        response = file_chooser.run()
        if response == Gtk.ResponseType.OK:
            filename = file_chooser.get_filename()
            values.pythonPath = filename
            file_button.set_label(os.path.basename(filename)) #set the button to show the filename
        file_chooser.destroy()


    def on_checkpoint_file_clicked(self, dialog, values, widget):
        file_chooser = Gtk.FileChooserDialog(
            title="Select Checkpoint File",
            parent=dialog,
            action=Gtk.FileChooserAction.OPEN
        )
        file_button = widget  # Use the widget argument here.
        # file_button.set_label(os.path.basename(filename)) # We don't have a filename.
        file_chooser.add_buttons(
            Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL,
            Gtk.STOCK_OPEN, Gtk.ResponseType.OK
        )

        response = file_chooser.run()
        if response == Gtk.ResponseType.OK:
            filename = file_chooser.get_filename()  # Get the selected filename
            values.checkPtPath = filename       # Store it in your values object
            file_button.set_label(os.path.basename(filename)) #set the button to show the filename
        file_chooser.destroy()

    def onSegTypeChanged(self, segTypeDropDown, segTypeVals, otherWidgets1, otherWidgets2, widget):  # Add segTypeDropDown parameter
        segType = segTypeVals[segTypeDropDown.get_active()]  # Use segTypeDropDown here

    # Lots of code changes here for Gimp 3.0.
    def optionsDialog(self, image, boxPathDict):

        boxPathDict = getPathDict(image)  # Call getPathDict()
        if boxPathDict is None:  # Check if getPathDict() returned None
            logging.error("No paths found in the image. Cannot continue.")  # Or show a message box
            return None  # Return None from optionsDialog() as well

        boxPathNames = sorted(boxPathDict.keys())
        boxPathExist = len(boxPathNames) > 0
        isGrayScale = image.get_base_type() == Gimp.ImageBaseType.GRAY  # Correct
        scriptDir = os.path.dirname(os.path.abspath(__file__))
        configFilePath = os.path.join(scriptDir, 'segany_settings.json')
        
        # Correct way to get the GIMP top-level window:
        dialog = Gtk.Dialog('Segment Anything', None, modal=True) # Correct new way.
        values = DialogValue(configFilePath) # This needs to be defined somewhere.
        formatBinary = values.formatBinary
        
        # ... (Load values from config, no changes needed here)
        pythonPath = values.pythonPath
        logging.debug('pythonPath: %s' % pythonPath)
        modelType = values.modelType
        checkPtPath = values.checkPtPath
        maskType = values.maskType
        segType = values.segType
        isRandomColor = values.isRandomColor
        maskColor = values.maskColor
        selPtCnt = values.selPtCnt

        modelTypeVals = ['vit_h', 'vit_l', 'vit_b']
        modelTypeIdx = modelTypeVals.index(modelType)
        # ... (rest of the initial value setup)

        # Widget Creation (Updated)
        pythonFileLbl = getRightAlignLabel('Python3 Path:')
        pythonFileBtn = Gtk.Button(label='Select File')  # Use a Gtk.Button
        pythonFileBtn.connect('clicked', functools.partial(self.on_python_file_clicked,
                                                           dialog,
                                                           values))  # Use self
        if pythonPath is not None:
            pythonFileBtn.set_label(os.path.basename(pythonPath)) #set the button to show the filename

        modelTypeLbl = getRightAlignLabel('Checkpoint Type:')
        modelTypeDropDown = Gtk.ComboBoxText()  # Modern ComboBox
        for value in modelTypeVals:
            modelTypeDropDown.append_text(value)
        modelTypeDropDown.set_active(modelTypeIdx)

        checkPtFileLbl = getRightAlignLabel('Checkpoint Path:')
        checkPtFileBtn = Gtk.Button(label='Select File')  # Use a Gtk.Button
        checkPtFileBtn.connect('clicked', functools.partial(self.on_checkpoint_file_clicked,
                                                            dialog,
                                                            values))  # Use self
        if checkPtPath is not None:
            checkPtFileBtn.set_label(os.path.basename(checkPtPath)) #set the button to show the filename

        # All things mask
        maskTypeLbl = getRightAlignLabel('Mask Type:')
        maskTypeVals = ['rgba', 'gray']  # Or whatever mask types you support
                # Improved way to set maskTypeIdx:
        try:
            maskTypeIdx = maskTypeVals.index(values.maskType)
        except ValueError:  # Handle the case where values.maskType is not in maskTypeVals
            maskTypeIdx = 0  # Default to the first item (or some other appropriate default)
            logging.warning(f"Invalid mask type: {values.maskType}. Defaulting to {maskTypeVals[0]}.")

        maskTypeLbl = getRightAlignLabel('Mask Type:')
        maskTypeDropDown = Gtk.ComboBoxText()
        for value in maskTypeVals:
            maskTypeDropDown.append_text(value)
        maskTypeDropDown.set_active(maskTypeIdx)

        # All things in the selPts group.
        selPtsLbl = getRightAlignLabel('Selection Points:')
        selPtsEntry = Gtk.Entry()
        selPtsEntry.connect('key-press-event', kepPressNum)
        selPtsEntry.set_text(str(selPtCnt))  # Set a default value

        boxPathNameLbl, boxPathNameDropDown = None, None
        if boxPathExist:
            boxPathNameLbl = getRightAlignLabel('Box Path:')
            boxPathNameDropDown = Gtk.ComboBoxText()  # Modern ComboBox
            for value in boxPathNames:
                boxPathNameDropDown.append_text(value)
            boxPathNameDropDown.set_active(0)

        # All things in the segType group.    
        segTypeLbl = getRightAlignLabel('Segmentation Type:')
        segTypeDropDown = Gtk.ComboBoxText()  # Modern ComboBox
        segTypeVals = ['Auto', 'Selection', 'Box-Selection', 'Box'] # Add this line! Define
        segTypeDropDown.connect('changed', functools.partial(self.onSegTypeChanged,
                                                             segTypeDropDown, segTypeVals,
                                                             [[selPtsLbl, selPtsEntry], [boxPathNameLbl, boxPathNameDropDown]],
                                                             [maskTypeLbl, maskTypeDropDown]))
        try:
            segTypeIdx = segTypeVals.index(values.segType)
        except ValueError:  # Handle the case where values.segType is not in segTypeVals
            segTypeIdx = 0  # Default to the first item (or some other appropriate default)
            logging.warning(f"Invalid segmentation type: {values.segType}. Defaulting to {segTypeVals[0]}.")

        for value in segTypeVals:
            segTypeDropDown.append_text(value)
        segTypeDropDown.set_active(segTypeIdx)

        # Other actions
        if not isGrayScale:
            maskColorLbl = getRightAlignLabel('Mask Color:')
            colHexVal = '#' + ''.join([('%x' % int(c)).zfill(2) for c in maskColor[:3]])
            gtkColor = Gdk.RGBA()
            try:
                gtkColor.parse(colHexVal)  # Use the parse() method
            except Exception as e:
                logging.error(f"Error parsing color: {str(e)}")
                gtkColor.parse("#FF0000") # Default to red if error
            maskColorBtn = Gtk.ColorButton(rgba=gtkColor)

            randColBtn = Gtk.CheckButton(label='Random Mask Color')
            randColBtn.set_active(isRandomColor)
            randColBtn.connect('toggled', onRandomToggled, [maskColorLbl, maskColorBtn])

        # Create the Format Binary checkbox:
        formatBinaryCheckBox = Gtk.CheckButton(label='Format Binary')  # Add a label
        formatBinaryCheckBox.set_active(values.formatBinary)  # Set initial state
        
        # Layout (Updated - Use Gtk.Grid)
        grid = Gtk.Grid()
        grid.set_column_spacing(5)  # Add some spacing
        grid.set_row_spacing(5)
        rowIdx = 0

        grid.attach(pythonFileLbl, 0, rowIdx, 1, 1) # Updated layout
        grid.attach(pythonFileBtn, 1, rowIdx, 1, 1)
        rowIdx += 1

        grid.attach(modelTypeLbl, 0, rowIdx, 1, 1)
        grid.attach(modelTypeDropDown, 1, rowIdx, 1, 1)
        rowIdx += 1

        grid.attach(checkPtFileLbl, 0, rowIdx, 1, 1)
        grid.attach(checkPtFileBtn, 1, rowIdx, 1, 1)
        rowIdx += 1

        grid.attach(maskTypeLbl, 0, rowIdx, 1, 1)
        grid.attach(maskTypeDropDown, 1, rowIdx, 1, 1)
        rowIdx += 1

        grid.attach(segTypeLbl, 0, rowIdx, 1, 1)
        grid.attach(segTypeDropDown, 1, rowIdx, 1, 1)
        rowIdx += 1

        rowIdx += 1
        grid.attach(selPtsLbl, 0, rowIdx, 1, 1)
        grid.attach(selPtsEntry, 1, rowIdx, 1, 1)
        rowIdx += 1
        # ... (Attach other widgets to the grid similarly)

        if boxPathExist: # Updated layout
            grid.attach(boxPathNameLbl, 0, rowIdx, 1, 1)
            grid.attach(boxPathNameDropDown, 1, rowIdx, 1, 1)
            rowIdx += 1

        if not isGrayScale: # Updated layout
            grid.attach(randColBtn, 1, rowIdx, 1, 1)
            rowIdx += 1

            grid.attach(maskColorLbl, 0, rowIdx, 1, 1)
            grid.attach(maskColorBtn, 1, rowIdx, 1, 1)
            rowIdx += 1

            onRandomToggled(randColBtn, [maskColorLbl, maskColorBtn])

        # ... (Layout code - add the checkbox to the dialog layout)
        grid.attach(formatBinaryCheckBox, 0, rowIdx, 2, 1)  # Attach to grid
        rowIdx += 1
        
        # ... (Rest of the dialog setup)

        hbox = Gtk.HBox()
        hbox.pack_start(grid, False, False, 0) # Use the grid

        
        # ... (Rest of the dialog setup)
        dialog.add_button(Gtk.STOCK_OK, Gtk.ResponseType.OK)
        dialog.add_button(Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL)

        dialog.vbox.pack_start(hbox, True, True, 0)

        dialog.show_all()
        segTypeDropDown.connect('changed',    functools.partial(self.onSegTypeChanged,  # Your callback
                                              segTypeDropDown, segTypeVals,         # Pre-filled arguments
                                              [[selPtsLbl, selPtsEntry], [boxPathNameLbl, boxPathNameDropDown]],
                                              [maskTypeLbl, maskTypeDropDown]))

        self.onSegTypeChanged(segTypeDropDown, segTypeVals,
                         [[selPtsLbl, selPtsEntry],
                          [boxPathNameLbl, boxPathNameDropDown]],
                          [maskTypeLbl, maskTypeDropDown], None)



        while True: # Updated
            response = dialog.run()
            if response == Gtk.ResponseType.OK:
                values.pythonPath = pythonFileBtn.get_label()  # Get filename from button label
                values.modelType = modelTypeVals[modelTypeDropDown.get_active()]
                values.checkPtPath = checkPtFileBtn.get_label()  # Get filename from button label
                values.segType = segTypeVals[segTypeDropDown.get_active()]
                values.maskType = maskTypeVals[maskTypeDropDown.get_active()]
                if not isGrayScale:
                    maskColor = maskColorBtn.get_rgba()
                    values.maskColor = maskColor
                values.selPtCnt = int(selPtsEntry.get_text())
                if boxPathExist:
                    values.selBoxPathName = boxPathNames[boxPathNameDropDown.get_active()]
                values.formatBinary = formatBinaryCheckBox.get_active()    
                valid = validateOptions(image, values) # Need to see this
                if not valid:
                    continue
                values.persist() # Need to see this
            else:
                values = None
            break

        dialog.destroy()
        return values
    
    def run(self, procedure, run_mode, image, drawables, config, run_data):
        """
        This is the main function that executes your plugin's logic.

        Args:
            procedure (Gimp.Procedure): The Gimp.Procedure object representing your plugin.
            run_mode (Gimp.RunMode): The run mode of the plugin (e.g., INTERACTIVE, NONINTERACTIVE).
            image (Gimp.Image): The Gimp.Image object that the plugin is being applied to.
            drawables (list of Gimp.Drawable): A list of Gimp.Drawable objects (layers, channels, etc.) that are currently selected.
            config (Gimp.ProcedureConfig): A Gimp.ProcedureConfig object (if your plugin uses configuration options).
            run_data (Gimp.RunData):  A Gimp.RunData object for passing data between run calls.
        """

        pdb = Gimp.PDB  # GIMP's Procedure Database
        level = logging.DEBUG
        configLogging(level)

        # Redirect sys.settrace output to a file
        trace_file = open("/tmp/gimp_trace.log", "w")  # Open a file for writing

        def trace_calls(frame, event, arg):
            if event == 'call':
                trace_file.write(f"Call to {frame.f_code.co_name} in {frame.f_code.co_filename}:{frame.f_code.co_firstlineno}\n")
            elif event == 'return':
                trace_file.write(f"Return from {frame.f_code.co_name} in {frame.f_code.co_filename}:{frame.f_code.co_firstlineno}\n")
            return trace_calls

        sys.settrace(trace_calls)

        # 1. Get parameters from the dialog:
        boxPathDict = getPathDict(image)
        values = self.optionsDialog(image, boxPathDict)
        if values is None:  # Cancelled
            sys.settrace(None) # Disable tracing
            return procedure.new_return_values(Gimp.PDBStatusType.CANCEL, GLib.Error())

        # 2. Use the parameters in your plugin logic:
        # Example: Accessing values from the dialog:
        pythonPath = values.pythonPath
        checkPtPath = values.checkPtPath
        modelType = values.modelType
        segType = values.segType
        maskType = values.maskType
        isRandomColor = values.isRandomColor
        maskColor = values.maskColor
        selPtCnt = values.selPtCnt
        selBoxPathName = values.selBoxPathName
        formatBinary = values.formatBinary

        # Example: Using the image and drawables:
        try:   
            width = image.get_width()
            height = image.get_height()
            # Define maskFileNoExt at the beginning of the run function.
            if image.get_file():
                maskFileNoExt = os.path.splitext(image.get_file().get_path())[0]
            else:
                maskFileNoExt = tempfile.NamedTemporaryFile().name
        

            # Get ONLY the layers:  Wierd syntax.  
            layers = image.get_layers()
            num_layers = len(layers)
            print("Width: ", width, "Height: ", height, "Layers: ", num_layers)
            
            for layer in layers:  # More descriptive variable name
                layer_name = layer.get_name()
                if layer_name is None: # Example of checking for an error condition.
                    raise ValueError("Layer name is None")
            # Initialize SegmentAnythingProcessor
            processor = SegmentAnythingProcessor(modelType, checkPtPath)

            # Prepare arguments for run_segmentation
            image_path = image.get_file().get_path() if image.get_file() else tempfile.NamedTemporaryFile(suffix=".png").name
            sel_file = None
            box_cos = None

            if segType == 'Selection':
                temp_sel_file = tempfile.NamedTemporaryFile(mode='w+', delete=False, suffix='.sel')
                if exportSelection(image, temp_sel_file.name, selPtCnt):
                    sel_file = temp_sel_file.name
                else:
                    return return_plugin_error(procedure, f"Selection export failed.")
                temp_sel_file.close()
            elif segType == 'Box-Selection' or segType == 'Box':
                box_cos = getBoxCos(image, boxPathDict, selBoxPathName)
                if not box_cos:
                    return return_plugin_error(procedure, f"Box coordinates retrieval failed.")

            # Run segmentation using SegmentAnythingProcessor
            processor.run_segmentation(
                image_path,
                segType,
                maskType,
                maskFileNoExt,
                'True' if formatBinary else 'False',
                sel_file=sel_file,
                box_cos=box_cos
            )
            # Note; there is an unknown issue the SAM code where upon return, it would never execute this layer
            # Call createLayers with the correct arguments
            # layerCount = createLayers(image, maskFileNoExt, values.userSelColor, formatBinary)
            # logging.info(f"{layerCount} layers created.")
            # So instead, lets try inlining it.
            print("Flushing Display") # debug print.
            Gimp.Display.flush() # Force display update.

            print("Creating Layers Inline") # debug print

            # Start move createLayer logic inline.
            width, height = image.get_width(), image.get_height()
            logging.info(f"In createLayers: {maskFileNoExt}")
            idx = 0
            maxLayers = 99999
            print("Before parent")
            parent = Gimp.LayerGroup.new(image)
            print("After parent")
            image.insert_layer(parent, None, 0)
            parent.set_opacity(50)

            uniqueColors = getRandomColor(layerCnt=999)

            if image.get_base_type() == Gimp.ImageType.GRAY:
                layerType = Gimp.ImageType.GRAYA
                userSelColor = [100, 255]
            else:
                layerType = Gimp.ImageType.RGBA
            logging.info(f"createLayers: {width},{height} {maskFileNoExt}")

            while idx < maxLayers:
                filepath = maskFileNoExt + str(idx) + '.seg'
                logging.debug(f"Layer source filepath: {filepath}")
                if os.path.exists(filepath):
                    logging.info(f"Creating Layer: {(idx + 1)}")
                    newlayer = Gimp.Layer.new(image, f"Segment Auto {idx}", layerType, 100, Gimp.LayerMode.NORMAL)
                    image.insert_layer(newlayer, parent, 0)
                    newlayer.set_visible(False)
                    buffer = newlayer.get_buffer()

                    maskVals = readMaskFile(filepath, formatBinary)
                    maskColor = userSelColor if userSelColor is not None else list(uniqueColors[idx]) + [255]

                    buffer.begin_write()
                    for y, line in enumerate(maskVals):
                        for x, p in enumerate(line):
                            if p:
                                buffer.set_pixel(x, y, maskColor)
                            else:
                                buffer.set_pixel(x, y, [0, 0, 0, 0] if layerType == Gimp.ImageType.RGBA else [0, 0])
                    buffer.end_write()

                    newlayer.flush()
                    newlayer.merge_shadow(True)
                    newlayer.update(0, 0, width, height)

                else:
                    break
                idx += 1

            logging.info(f"{idx} layers created.") # layerCount logging.
 
        except AttributeError as e:
            try:
                error_str = str(e).encode('utf-8').decode('utf-8')
            except UnicodeEncodeError:
                error_str = str(e).encode('latin-1', 'replace').decode('latin-1') # fallback to latin-1
                return return_plugin_error(procedure, f"AttributeError: {error_str}")
            
        except ValueError as e:
            try:
                error_str = str(e).encode('utf-8').decode('utf-8')
            except UnicodeEncodeError:
                error_str = str(e).encode('latin-1', 'replace').decode('latin-1') # fallback to latin-1
                return return_plugin_error(procedure, f"ValueError: {error_str}")

        except FileNotFoundError as e:
            return return_plugin_error(procedure, f"Could not open configuration file. Please check if the file exists.")  # Use helper

        except json.JSONDecodeError as e:
            return return_plugin_error(procedure, "Invalid JSON format in configuration file.")  # Use helper function

        except subprocess.CalledProcessError as e:
            return return_plugin_error(procedure, "An error occurred during segmentation. Please check the plugin settings.")  # Use helper

        except Exception as e:
            return return_plugin_error(procedure, "An unexpected error occurred. Please check the pluin logs for details.")

        return procedure.new_return_values(Gimp.PDBStatusType.SUCCESS)

# Register the plugin with GIMP.   It's just not that simple anymore.
#Gimp.register(
#    "python_fu_seg_any",  # Name
#    "Segment Anything Mask Layers",  # Blurb
#    "Create Layers With Masks Generated By Segment Anything",  # Help
#    "Shrinivas Kulkarni",  # Author
#    "Chuck Sites and GEM 2025",  # Copyright
#    "2023",  # Date
#    "<Image>/Image/Segment Anything Layers...",  # Menu path
#    "RGB*, GRAY*",  # Image types
#    [],  # Parameters
#    [],  # Return values
#    SegAny # Plugin class
#)

Gimp.main(SegAny.__gtype__, sys.argv)
