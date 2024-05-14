#!/usr/bin/python

import ifflib
import struct
import io
import re
import sys
import typing
import numpy as np
from dataclasses import dataclass
from pathlib import Path
from PIL import Image
from configparser import ConfigParser
from collections import OrderedDict

# check if running as a PyInstaller exe
try:
    import pyi_splash
    is_exe = True
    pyi_splash.close()
except Exception:
    is_exe = False

#is_exe=True
verbose = False

frame_number_re = re.compile(r"fram_(\d{1,4})")

max_alpha = lambda p: Pixel(*(p.values()[:3]))

def resource_path(relative_path):
    """ Get absolute path to resource, works for dev and for PyInstaller """
    try:
        # PyInstaller creates a temp folder and stores path in _MEIPASS
        base_path = Path(sys._MEIPASS)
    except Exception:
        base_path = Path('.').resolve(strict=True)
    #print(f'returning {base_path / relative_path}')
    return (base_path / relative_path).resolve()

def read_line_length(in_fh: io.BufferedReader):
    rawlen = in_fh.read(2)
    if len(rawlen) < 2:
        return 0
    if rawlen[0] & 0x80 != 0:
        (length,) = struct.unpack(">H", rawlen)
        return length & 0x7fff
    else:
        in_fh.seek(-1, 1)
        return rawlen[0]

def get_sprite_line_info(in_fh: io.BufferedReader):
    data = in_fh.read(3)
    if len(data) < 3:
        return {"data_length": 0}
    return {"data_length": data[0],
            "start": data[1],
            "pixel_length": data[2]}

def getRunData(byte):
    flag = byte >> 5
    length = byte & 31
    return (flag, length)

@dataclass
class Pixel:
    """Class for managing pixel values in different formats"""
    red: int
    green: int
    blue: int
    alpha: int = 0xff

    @classmethod
    def from_int(cls, half_word: int):
        # Shift values move each colour channel to the right
        # place value.
        # Bitwise AND masks off the unwanted bits to leave
        # only the desired channel.
        
        blue = round((half_word & 0b11111) / 31 * 255)
        green = round(((half_word >> 5) & 0b111111) / 63 * 255)
        red = round(((half_word >> 11) & 0b11111) / 31 * 255)

        return cls(red, green, blue)
    
    def copy(self):
        return Pixel(self.red, self.green, self.blue, self.alpha)
    
    def to_int(self):
        r5 = round(self.red / 255 * 31)
        g6 = round(self.green / 255 * 63)
        b5 = round(self.blue / 255 * 31)
        a5 = round(self.alpha / 255 * 31)
        
        return (r5, g6, b5, a5)
    
    def values(self):
        return (self.red, self.green, self.blue, self.alpha)

    def pack_to_bin(self, format: str ="RGB") -> bytes:
        match format:
            case "RGB":
                return struct.pack("BBB", self.red, self.green, self.blue)
            case "RGBA":
                return struct.pack("BBBB", self.red, self.green, self.blue, self.alpha)
            case _:
                raise Exception("Invalid pixel format specifier")

shadow = Pixel(0, 0, 0, 0x80)
transparency = Pixel(0x00, 0x00, 0x00, 0x00)

def load_player_colors(filename: str = "data/COLORS.INI"):
    c_file = ConfigParser()
    c_file.read(resource_path(filename))
    player_cols = {}
    c_name_re = re.compile(r"color_(\d{1,2})_shade_(\d{1,2})")
    c_value_re = re.compile(r"\W*(\d{1,3}),(\d{1,3}),(\d{1,3})")
    for color in c_file['PlayerColors']:
        name_match = c_name_re.match(color)
        value_match = c_value_re.match(c_file['PlayerColors'][color])
        if name_match and value_match:
            player_num = int(name_match.group(1))
            shade_num = int(name_match.group(2))
            color = value_match.group(1,2,3)
            i_color = tuple(int(c) for c in color)
            if player_num not in player_cols.keys():
                player_cols[player_num] = {shade_num: Pixel(*i_color)}
            else:
                player_cols[player_num][shade_num] = Pixel(*i_color)
    return player_cols

player_cols = load_player_colors()

def packPixel(value=(0,0,0), alpha=False):
    if len(value) < 3:
        raise ValueError("Not enough pixel data")
    if alpha:
        if len(value) == 3:
            return struct.pack("BBBB", value[0], value[1], value[2], 0xff)
        return struct.pack("BBBB", value[0], value[1], value[2], value[3])
    return struct.pack("BBB", value[0], value[1], value[2])

def decodePixel(half_word: int):
    # Shift values move each colour channel to the right
    # place value.
    # Bitwise AND masks off the unwanted bits to leave
    # only the desired channel.
    blue = round((half_word & 0b11111) / 31 * 255)
    green = round(((half_word >> 5) & 0b111111) / 63 * 255)
    red = round(((half_word >> 11) & 0b11111) / 31 * 255)
    return Pixel(red, green, blue)

class Line:
    def __init__(self, in_fh: io.BufferedReader, sprite=False):
        _ = sprite
        header_offset = in_fh.tell()
        total_length = read_line_length(in_fh)
        self.transparent_pixels = read_line_length(in_fh)
        self.pixel_length = read_line_length(in_fh)
        self.offset = in_fh.tell()
        self.data_length = total_length - (self.offset - header_offset)
                
class Frame:
    def __init__(self, size, in_fh: io.BufferedReader):
        self.size = size
        self.lines = []
        while len(self.lines) < self.size[1]:
            newline = Line(in_fh, False)
            #if newline.data_length == 0:
            #    continue
            self.lines.append(newline)
            #if len(self.lines) >= self.size[1]:
            #    break
            in_fh.seek(newline.offset + newline.data_length)

class tgrFile:
    """
    A class representing a .TGR game asset file,
    which as a format is based on the IFF file structure
    """
    def __init__(self, filename: str, is_sprite=False):
        self.filename = Path(filename)
        self.read_from = self.filename.suffix.upper()
        #self.read_from = read_from
        match self.read_from:
            case '.TGR':
                self.iff = ifflib.iff_file(self.filename)
            case '.PNG':
                self.imgs = []
                self.imgs.append(Image.open(self.filename))
            case '':
                filelist = list(self.filename.glob('*'))
                self.imgs = [None for _ in range(len(filelist))]
                print(f'Loading files from {self.filename.resolve()}')
                for f in filelist:
                    match f.suffix.upper():
                        case ".PNG":
                            self.read_from = ".PNG" if self.read_from == '' else self.read_from
                            m = frame_number_re.match(str(f.stem))
                            if m:
                                fram_number = int(m.group(1))
                            else:
                                print(f"Failed to find fram number from {f.stem}")
                                exit(1)
                            print('>', f, fram_number)
                            # Maybe move opening the file into the load function
                            img = Image.open(f)
                            if img.mode != 'RGBA':
                                img = img.convert('RGBA')
                            self.imgs[fram_number] = img
                        case '.INI':
                            print(f'Skipping {f.stem + f.suffix}')
                            # Shortens list to prevent crashes when reading a NoneType object
                            del self.imgs[-1]
                        case _:
                            print(f"Error: invalid file type {f.suffix}")
                            del self.imgs[-1]
            case _:
                print(f"Error: invalid read type {self.read_from}")
                
        self.size: typing.Tuple[int, int] = (0,0)
        self.is_sprite = is_sprite
        self.framesizes = []
        self.frameoffsets = []
        self.frames = []
        self.padding_frames = []

    def load(self, config_path: str|None=None, no_crop=False):
        match self.read_from:
            case '.TGR':
                self.iff.load()
                if self.iff.data.formtype != "TGAR":
                    print(f"Error: invalid file type: {self.iff.data.formtype}")
                self.read_header()
                if self.indexed_colour:
                    self.load_palette()
                self.get_frames()
            case '.PNG':
                self.read_config(config_path)
                self.img_data = [[] for _ in range(len(self.imgs))]
                self.size = self.imgs[0].size
                for index, img in enumerate(self.imgs):
                    if index in self.padding_frames:
                        self.framesizes.append([0, 0, 0xFFFF, 0xFFFF, 0xFFFF, 0xFFFF])
                    else:
                        if img.size != self.size:
                            raise ValueError(f"Frame:{index} size:{img.size} doesn't match Frame:0 size:{self.size}")
                        if not no_crop:
                            # from https://stackoverflow.com/a/67677468
                            img_array = np.array(img)
                            # Find indices of non-transparent pixels (indices where alpha channel value is above zero).
                            idx = np.where(img_array[:, :, 3] > 0)
                            # Get minimum and maximum index in both axes (top left corner and bottom right corner)
                            x0, y0, x1, y1 = idx[1].min(), idx[0].min(), idx[1].max(), idx[0].max()
                            # Crop rectangle and convert to Image
                            img = Image.fromarray(img_array[y0:y1+1, x0:x1+1, :])
                            self.framesizes.append([x1-x0+1, y1-y0+1, x0, y0, x1, y1])  # +1 includes both endpoints
                        else:
                            self.framesizes.append([img.size[0], img.size[1], 0, 0, img.size[0]-1, img.size[1]-1])
                        self.img_data[index] = img.getdata()
                    

    def read_header(self):
        with open(self.filename, "rb") as in_fh:
            in_fh.seek(self.iff.data.children[0].data_offset)
            (self.version,
             self.framecount,
             self.bits_per_px) = struct.unpack("IHBx", in_fh.read(8))
            (index_mode,
             self.offset_flag) = struct.unpack("xBBx", in_fh.read(4))
            self.size = struct.unpack("HH", in_fh.read(4))
            self.hotspot = struct.unpack("HH", in_fh.read(4))
            print(f'Image size: {self.size}')
            
            #print(self.offset_flag)
            self.indexed_colour = index_mode & 0x7f == 0x1a
            self.bounding_box = [*struct.unpack('HHHH',in_fh.read(8))]
            in_fh.seek(12, 1)
            #if self.indexed_colour:
            #    in_fh.seek(12, 1)
            for _ in range(self.framecount):
                (ulx, uly, lrx, lry, offset) = struct.unpack("HHHHI", in_fh.read(12))
                # Skip empty frames (offset will be zero)
                if offset == 0:
                    self.framesizes.append((0, 0, 0))
                    self.frameoffsets.append(((0, 0), (0, 0)))
                    print(f'Frame {_} is a padding frame. Leave frame as-is to avoid packing errors')
                else:
                    self.framesizes.append((1+lrx-ulx, 1+lry-uly, offset))
                    self.frameoffsets.append(((ulx, uly), (lrx, lry)))
            
            self.anim_count = struct.unpack('H',in_fh.read(2))[0]
            self.animations = []
            for _ in range(self.anim_count):
                #(start_frame, frame_count, frame_rate) = struct.unpack('HHH', in_fh.read(6))
                self.animations.append([*struct.unpack('HHH', in_fh.read(6))])
                
        #print(len(self.framesizes))

    def load_palette(self):
        palt = self.iff.data.children[1]
        self.palette = []
        with open(self.filename, "rb") as in_fh:
            in_fh.seek(palt.data_offset)
            (count,) = struct.unpack("<H", in_fh.read(2))
            print(f'Colors in Palette: {count}')
            for _ in range(count):
                raw_pixel = in_fh.read(2)
                if len(raw_pixel) < 2:
                    raise ValueError("Not enough image data")
                (pixel,) = struct.unpack("H", raw_pixel)
                self.palette.append(Pixel.from_int(pixel))

    def get_frames(self):
        with open(self.filename, "rb") as in_fh:
            for child in self.framesizes:
                in_fh.seek(child[2])
                newframe = Frame((child[0], child[1]), in_fh)
                self.frames.append(newframe)

    def get_next_pixel(self, in_fh: io.BufferedReader):
        if self.indexed_colour:
            (pixel_ix,) = struct.unpack("B", in_fh.read(1))
            return self.palette[pixel_ix]
        else:
            (raw_pixel,) = struct.unpack("H", in_fh.read(2))
            return Pixel.from_int(raw_pixel)

    def extractLine(self, fh: io.BufferedReader, frame_index=0, line_index=0, increment=0, color=2, fx_error_fix=False):
        outbuf = []
        line_ix = 0
        pixel_ix = 0
        line = self.frames[frame_index].lines[line_index]
        fh.seek(line.offset)
        # print(f"Extracting line of length 0x{line.pixel_length:x}")
        for _ in range(line.transparent_pixels):
            outbuf.append(transparency)
        pixel_ix += line.transparent_pixels
        
        while line_ix < line.data_length:# and pixel_ix < line.pixel_length:
            run_header = fh.read(1)
            line_ix += 1
            (flag, run_length) = getRunData(run_header[0])
            
            if fx_error_fix:
                if run_header[0] in (0x7F, 0xFD):
                    outbuf.append(Pixel(255, 0, 255, 0))
                    pixel_ix += 1
                    continue
                    
            match flag:
                case 0b000:
                    outbuf += [transparency for _ in range(run_length + increment)]
                case 0b001:
                    pixel = self.get_next_pixel(fh)
                    outbuf += [pixel.copy() for _ in range(run_length+increment)]
                    pixel_ix += run_length+increment
                    line_ix += self.bits_per_px // 8
                case 0b010:
                    for _ in range(run_length+increment):
                        outbuf.append(self.get_next_pixel(fh))
                        line_ix += self.bits_per_px // 8
                        pixel_ix += 1
                case 0b011:
                    alpha_raw = fh.read(1)[0] & 31
                    alpha = round((alpha_raw / 31) * 255)
                    line_ix +=1
                    pixel = self.get_next_pixel(fh)
                    pixel.alpha = alpha
                    outbuf += [pixel.copy() for _ in range(run_length+increment)]
                    pixel_ix += run_length+increment
                    line_ix += self.bits_per_px // 8
                case 0b100:
                    pixel = self.get_next_pixel(fh)
                    pixel.alpha = round(run_length / 31 * 255)
                    outbuf.append(pixel.copy())
                    line_ix += self.bits_per_px // 8
                    pixel_ix += 1
                case 0b101:
                    #outbuf.append(Pixel(0xff, 0x00, 0xff))
                    outbuf += [shadow for _ in range(run_length + increment)]
                case 0b110:
                    #print(f"flag 6 at 0x{fh.tell()-1:08x}")
                    outbuf.append(player_cols[color][run_length])
                    pixel_ix += 1
                case 0b111:
                    # check if run or single translucent
                    if verbose:
                        print(f'{line_index},{pixel_ix} ({line_ix}): reading 0b111 with run_length {run_length}')
                    if run_length > 27:
                        byte = fh.read(1)[0]
                        alpha = byte & 31
                        color_index = (byte >> 3 & 0b11100) | (run_length & 3)
                        # create new pixel object to avoid shallow copying
                        pixel = Pixel(*player_cols[color][color_index].values())
                        pixel.alpha = round(alpha / 31 * 255)
                        outbuf.append(pixel.copy())
                        pixel_ix += 1
                        line_ix += 1
                    else:
                        read_length = (run_length + 1) // 2
                        color_index = fh.read(read_length)
                        line_ix += read_length
                        
                        for i, b in enumerate(color_index):
                            # splits the byte into two 4bit sections, shifts left 1bit, and sets least sig to 1
                            # then uses as index for player color value
                            outbuf.append(player_cols[color][((b >> 3) & 0b11111) | 0b1])
                            pixel_ix += 1
                            # Don't append trailing null padding on odd run lengths
                            if (run_length % 2 == 0) or (i < len(color_index) - 1):
                                outbuf.append(player_cols[color][((b << 1) & 0b11111) | 0b1])
                                pixel_ix += 1                    
                case _:
                    print(f"{line_index:3d},{pixel_ix:3d}: Unsupported flag {flag} in datapoint 0x{run_header[0]:02x} at offset 0x{fh.tell()-1:08x}")
        if len(outbuf) < line.pixel_length:
            print(f"Appending {line.pixel_length - len(outbuf)} pixels to line {line_index}")
            outbuf += [transparency for _ in range(line.pixel_length - len(outbuf))]
        return outbuf
    
    def read_config(self, config_path: str|None=None):
        config = ConfigParser()
        if not config_path:
            config_path = f"{self.filename} / 'sprite.ini'"
        config.read(config_path)
        self.bits_per_px = int(config['BitDepth']['Depth'])
        self.hotspot = (int(config['HotSpot']['X']), int(config['HotSpot']['Y']))
        self.bounding_box = (int(config['BoundingBox']['XMin']), int(config['BoundingBox']['YMin']), int(config['BoundingBox']['XMax']), int(config['BoundingBox']['YMax']))
        
        if len(config['PaddingFrames']['FrameList']) > 0:
            self.padding_frames = list(map(int, config['PaddingFrames']['FrameList'].split(',')))
        #else:
        #    self.padding_frames = []
        
        self.animations = [(0, 0, 0, 0) for _ in range(6)]
        self.anim_count = 0
        anim_number_re = re.compile(r"Animation(\d{1,1})")
        
        for k, v in config.items():
            m = anim_number_re.match(k)
            if m:
                anim_number = int(m.group(1))
                if anim_number >= self.anim_count:
                    self.anim_count = anim_number + 1
                self.animations[anim_number] = (int(config[f'Animation{anim_number}']['StartFrame']), int(config[f'Animation{anim_number}']['FrameCount']), int(config[f'Animation{anim_number}']['AnimationCount']))
        
        self.animations = self.animations[:self.anim_count]
        # print(self.anim_count, self.animations)
            
        
    
    def write_config(self, config_path: str|None=None):
        if config_path == None:
            config_path = f'{self.filename.stem}/sprite.ini'
        config = ConfigParser(dict_type=OrderedDict, allow_no_value=True)
        config.optionxform = str
        config.add_section('Description')
        config.set('Description', (f'; This file contains metadata for the extracted sprite {self.filename.stem+self.filename.suffix}\n'+
                                   '; This allows the sprite to be repacked into a .TGR'))

        config.add_section('BitDepth')
        config.set('BitDepth', ('; BitDepth is the number of bits used to encode each pixel color.\n'+
                                '; This will be 16 if the sprite uses direct color and 8 if it uses a color palette'))
        # hardcoded to 16 because repacking with a palette is not currently supported
        config.set('BitDepth', 'Depth', '16')
        
        config.add_section('HotSpot')
        config.set('HotSpot', '; HotSpot is the position the sprite is displayed at in-game relative to the game object')
        config.set('HotSpot', 'X', str(self.hotspot[0]))
        config.set('HotSpot', 'Y', str(self.hotspot[1]))
        
        config.add_section('BoundingBox')
        config.set('BoundingBox', '; BoundingBox is the clickable region of the sprite')
        config.set('BoundingBox', 'XMin', str(self.bounding_box[0]))
        config.set('BoundingBox', 'YMin', str(self.bounding_box[1]))
        config.set('BoundingBox', 'XMax', str(self.bounding_box[2]))
        config.set('BoundingBox', 'YMax', str(self.bounding_box[3]))
        
        config.add_section('PaddingFrames')
        config.set('PaddingFrames', ('; Building sprites reserve frames 0 through 2 for specific purposes:\n'+
                                     '; Frame 0 is the base sprite. This is always displayed\n'+
                                     '; Frame 1 is the wall overlay sprite\n'
                                     '; Frame 2 is always left as a zero-length padding frame'))
        config.set('PaddingFrames', 'FrameList', ','.join(map(str, self.padding_frames)))
        
        config.add_section('Animations')
        config.set('Animations', ('; Sprites can have up to six animations, each consisting of a Start Frame, Frame Count, and Animation Count\n'+
                                  '; Start Frame is the first frame of the West-facing version of the animation. Subsequent versions are in counterclockwise order\n'+
                                  '; Frame Count is the number of frames in each version of the animation\n'+
                                  '; Animation Count is the number of different versions of the animation. The default is 8 for Walk, Attack0, and Attack1, and 4 for Die, Idle, and Rot\n'+
                                  '; Animation0 is Walk for units and projectiles, and is the default animation for buildings\n'+
                                  '; Animation1 is Attack0 for units\n'+
                                  '; Animation2 is Die for units and projectiles\n'+
                                  '; Animation3 is Idle for units\n'+
                                  '; Animation4 is Attack1 for units\n'+
                                  '; Animation5 is Rot for units and projectiles'))
        for i in range(self.anim_count):
            config.add_section(f'Animation{i}')
            config.set(f'Animation{i}', 'StartFrame', str(self.animations[i][0]))
            config.set(f'Animation{i}', 'FrameCount', str(self.animations[i][1]))
            config.set(f'Animation{i}', 'AnimationCount', str(self.animations[i][2]))
        
        with open(config_path, 'w') as c_fh:
            config.write(c_fh)

    def look_ahead(self, p: Pixel, frame_index, line_index, pixel_ix, matching=True, color=None, translucent=False):
        collected = 0
        if matching:
            if verbose and frame_index == 0:
                print(f'frame_index:{frame_index} (max:{len(self.img_data)}) pixel:{pixel_ix + collected + 1} (max:{self.framesizes[frame_index][0]}) total:{line_index*self.framesizes[frame_index][0] + pixel_ix + collected + 1} (max:{len(self.img_data[frame_index])}) size_data:{self.framesizes[frame_index]}')
            while (pixel_ix + collected + 1 < self.framesizes[frame_index][0]):
                next_pixel = Pixel(*self.img_data[frame_index][line_index*self.framesizes[frame_index][0] + pixel_ix + collected + 1])
                if p != next_pixel:
                    break
                if color and max_alpha(next_pixel) in player_cols[color].values():
                    break
                collected += 1
                if translucent and collected == 22:
                    break
                # Reduced from 30 for compatibility with certain gui sprites
                if collected == 23:
                    break
            return collected
        else:
            if pixel_ix == self.framesizes[frame_index][0] - 1:    # If last pixel in row:
                return 1                        # Return 1 pixel, don't compare
            while True:
                if pixel_ix + collected >= self.framesizes[frame_index][0]:
                    break
                this_pixel = Pixel(*self.img_data[frame_index][line_index*self.framesizes[frame_index][0] + pixel_ix + collected])
                next_pixel = Pixel(*self.img_data[frame_index][line_index*self.framesizes[frame_index][0] + pixel_ix + collected + 1])
                if this_pixel == next_pixel or this_pixel.alpha != 255:
                    break
                if color and max_alpha(this_pixel) in player_cols[color].values():
                    break
                if verbose and frame_index == 0:
                    print(f"\tLook_Ahead: pixel {this_pixel} at c:{pixel_ix + collected} doesn't match pixel {next_pixel} at c:{pixel_ix + collected + 1}")
                collected += 1
                if collected == 31:
                    break
            if verbose:
                print(f'      Look_Ahead: collected {collected} individual pixels')
            return collected
    
    def encodeLineHeader(self, frame_index, line_index, outbuf, ct_pixels, offset=0):
        #print(outbuf)
        line_length = len(outbuf)
        header_length = 3
        
        assert line_length <= 0x7FFA, f'f:{frame_index: >4} l:{line_index: >4} line length {line_length} exceeds 15 bit maximum'
        assert offset <= 0xFF, f'f:{frame_index: >4} l:{line_index: >4} offset to first non-padding pixel exceeds 8 bit maximum'
        assert ct_pixels <= 0x7FFF, f'f:{frame_index: >4} l:{line_index: >4} pixel count {ct_pixels} exceeds 15 bit maximum'
        
        if ct_pixels > 0x7F:
            ct_pixels = ct_pixels | 0x8000
            pfc = 'H'
            header_length += 1
        else:
            pfc = 'B'
        
        if line_length + header_length > 0x7F:
            line_length = line_length | 0x8000
            lfc = 'H'
            header_length += 1
        else:
            lfc = 'B'
        
        return struct.pack('>'+lfc+'B'+pfc, line_length+header_length, offset, ct_pixels) + outbuf
        
        
    def encodeLine(self, frame_index=0, line_index=0, color=None):
        if verbose:
            print(f"image size:{self.size}")
        pixel_ix = 0
        offset = 0      # Offset from edge of frame to first non-padding pixel
        ct_pixels = 0
        padding_complete = False
        outbuf = b''
        
        while pixel_ix < self.framesizes[frame_index][0]:
            if frame_index == 0:
                if verbose:
                    print(f'TOP OF LOOP: pixel_ix:{pixel_ix}')
            
            p = Pixel(*self.img_data[frame_index][line_index*self.framesizes[frame_index][0] + pixel_ix])
            if verbose:
                print(f'reading p:{p} at l:{line_index} c:{pixel_ix}')
                
            # Allows for offset to collect more than 31 pixels, set true once first non-padding pixel is reached
            if padding_complete == False and p != transparency:
                padding_complete = True
                
            if p == transparency:        # Encode transparent pixels
                if verbose:
                    print(f'  chose flag 0b000')
                run_length = self.look_ahead(p, frame_index, line_index, pixel_ix) + 1
                # collect all leading padding
                if not padding_complete:
                    offset += run_length
                    pixel_ix += run_length
                # Don't write trailing padding
                elif pixel_ix + run_length >= self.framesizes[frame_index][0]:
                    break
                else:
                    if run_length == 31:
                        print(f'31 transparent pixels found, begining scan-ahead at l:{line_index} p:{pixel_ix}')
                        collected = run_length
                        while pixel_ix + collected < self.framesizes[frame_index][0] and (ct := self.look_ahead(p, frame_index, line_index, pixel_ix + collected) + 1) == 31:
                            print(f'   read {ct} more, total is {collected}')
                            collected += ct
                        collected += ct  # ct won't have been added the final time
                        if pixel_ix + collected >= self.framesizes[frame_index][0]:
                            break
                    
                    flag = 0b000 << 5
                    header = flag + (run_length & 0b11111)
                    outbuf += struct.pack('<B', header)
                    pixel_ix += run_length
                    ct_pixels += run_length
                    if verbose:
                        print(f'  packing header {header:02X}')
            
                if verbose:
                    print(f'  advanced to c:{pixel_ix}')
                
            elif p == shadow:
                ct_shadow = self.look_ahead(p, frame_index, line_index, pixel_ix) + 1
                flag = 0b101 << 5
                header = flag + (ct_shadow & 0b11111)
                outbuf += struct.pack('<B', header)
                pixel_ix += ct_shadow
                ct_pixels += ct_shadow
            
            # Set alpha to 255 for compare so translucent PP will still match
            # color != None skips player pixels if no -c passed
            elif color != None and max_alpha(p) in player_cols[color].values():
                # encode translucent PP
                if p.alpha < 255:
                    flag = 0b111 << 5
                    fixed_bits = 0b111 << 2
                    color_index = list(player_cols[color].keys())[list(player_cols[color].values()).index(max_alpha(p))]
                    # split color_index for packing
                    ci_l = (color_index & 0b11)
                    ci_h = (color_index & 0b11100) << 3
                    a = p.to_int()[3] & 0b11111
                    header = flag + fixed_bits + ci_l
                    body = ci_h + a
                    outbuf += struct.pack('<BB', header, body)
                    print(f"  packing header {header:02X} and body {body:02X}")
                    pixel_ix += 1
                    ct_pixels += 1
                    
                    # encode opaque PP
                else:
                    if verbose:
                        if frame_index == 0:
                            print(f'matched pixel {p} in color list {color}')
                        print('  chose flag 0b110')
                    flag = 0b110 << 5
                    color_index = list(player_cols[color].keys())[list(player_cols[color].values()).index(p)]
                    header = flag + (color_index & 0b11111)
                    outbuf += struct.pack('B', header)
                    pixel_ix += 1
                    ct_pixels += 1
                    if verbose and frame_index == 0:
                        print(f'  packed {header:02X}, flag {flag}, index {color_index}')
                
            elif p.alpha < 255:     #Encode translucent pixels                    
                run_length = self.look_ahead(p, frame_index, line_index, pixel_ix, translucent=True) + 1
                (r,g,b,a) = p.to_int()
                if run_length == 1:
                    if verbose:
                        print(f'  chose flag 0b100')
                    flag = 0b100 << 5
                    header = flag + (a & 0b11111)
                    body = (r << 11) + (g << 5) + b
                    if verbose:
                        print(f"  packing header {header:02X} and body {body:04X}")
                    outbuf += struct.pack('<BH', header, body)
                else:
                    if verbose:
                        print(f'  chose flag 0b011')
                    flag = 0b011 << 5
                    header = flag + (run_length & 0b11111)
                    body = (r << 11) + (g << 5) + b
                    outbuf += struct.pack('<BBH', header, a, body)
                    if verbose:
                        print(f'  packing header {header:02X} alpha {a:02X} and body {body:04X}')
                pixel_ix += run_length
                ct_pixels += run_length
                if verbose:
                    print(f'  advanced to c:{pixel_ix}')
                    
            else:                   # Encode opaque pixels
                matching = self.look_ahead(p, frame_index, line_index, pixel_ix, color=color)
                if matching:
                    if verbose:
                        print(f'  chose flag 0b001')
                    flag = 0b001 << 5
                    run_length = matching + 1
                    header = flag + (run_length & 0b11111)
                    (r,g,b,a) = p.to_int()
                    body = (r << 11) + (g << 5) + b
                    outbuf += struct.pack('<BH', header, body)
                    pixel_ix += run_length
                    ct_pixels += run_length
                    if verbose:
                        print(f'  packing header {header:02X} and body {body:04X}\n  advanced to c:{pixel_ix}')
                
                else:
                    non_matching = self.look_ahead(p, frame_index, line_index, pixel_ix, matching=False, color=color)
                    if non_matching:
                        if verbose:
                            print(f'  chose flag 0b010')
                        run_length = non_matching
                        if verbose:
                            print(f'  found {run_length} unique pixels')
                        flag = 0b010 << 5
                        header = flag + (run_length & 0b11111)
                        outbuf += struct.pack('<B', header)
                        if verbose:
                            print(f'  packing header {header:02X}')
                        for i in range(0,run_length):
                            cur_pix = Pixel(*self.img_data[frame_index][line_index*self.framesizes[frame_index][0] + pixel_ix + i])
                            (r,g,b,a) = cur_pix.to_int()
                            if verbose:
                                print(f'    p:{cur_pix} r:{r} g:{g} b:{b} a:{a}')
                            body = (r << 11) + (g << 5) + b
                            outbuf += struct.pack('<H', body)
                            if verbose:
                                print(f'    packing body:{body:04X}')
                        pixel_ix += run_length
                        ct_pixels += run_length
                    
                    else:
                        print(f'f:{frame_index: >4} l:{line_index: >4} p:{pixel_ix} : could not pack {p}, defaulting to 0x0000')
                        header = 0b01000001
                        pixel = 0x0000
                        outbuf += struct.pack('<BH', header, body)
                        pixel_ix += 1
                        ct_pixels += 1
                        
                if verbose:
                        print(f'  advanced to c:{pixel_ix}')
        
        return self.encodeLineHeader(frame_index, line_index, outbuf, ct_pixels, offset=offset)    
        
    
    def encodeFrame(self, frame_index=0, color=None):
        outbuf = b''
        for line_index in range(0,self.framesizes[frame_index][1]):
            outbuf += self.encodeLine(frame_index=frame_index, line_index=line_index, color=color)
        
        # pad frame to 4-byte boundary
        if len(outbuf) % 4 != 0:
            outbuf += b'\x00' * (4 - (len(outbuf) % 4))
        
        return struct.pack('>II', 0x4652414D, len(outbuf)) + outbuf
    
    def calcHotSpot(self):
        if self.hotspot != (0,0):
            return self.hotspot
        if len(self.img_data) > 1:
            x = int(self.framesizes[0][0] / 2 + self.framesizes[0][2])
            y = int(self.framesizes[0][1])
        else:
            x = 0
            y = 0
        return (x,y)
    
    def calcPaletteOffset(self):
        # TODO
        return 0
    
    def packFrameSizes(self, anim_buf: bytes):
        offset_to_fram = 12 + 8 + 40 + len(self.img_data)*12 + len(anim_buf) + 8
        # FORM + HEDR header + HEDR body + expected frame sizes + animations + FRAM header
        outbuf = b''
        for s, o in zip(self.framesizes, self.frameoffsets):
            # make sure offset stays 0 for padding frames
            if s[2] == 0xFFFF and o == 0:
                o -= offset_to_fram
            outbuf += struct.pack('4HI',
                                 s[2],
                                 s[3],
                                 s[4],
                                 s[5],
                                 o + offset_to_fram
             )
        if len(outbuf) != len(self.img_data)*12:
            raise ValueError("Packed Frame Size {len(outbuf)} doesn't' matched expected size {len(self.img_data)*12}")
        return outbuf
    
    def packAnimations(self):
        data = struct.pack('<H', self.anim_count)
        for a in self.animations:
            data += struct.pack('HHH', a[0], a[1], a[2])
        
        if self.anim_count % 2 == 0:
            data += b'\x00' * 2

        return data
    
    def encodeHeader(self, frame_buffer: bytes):
        chunk_name = b'HEDR'
        chunk_length = 0
        version = 0x04
        frame_count = len(self.img_data)
        if self.bits_per_px == 8:
            index_mode = 0x001A
        else:
            index_mode = 0
        offset_flag = 0
        size_x = self.size[0]
        size_y = self.size[1]
        (hs_x, hs_y) = self.calcHotSpot()
        palette_offset = self.calcPaletteOffset()
        
        animations = self.packAnimations()
        frame_sizes = self.packFrameSizes(animations)
        #print(frame_sizes[:16])
        
# =============================================================================
#         out_text = (f'version:{type(version)}\n'+
#                     f'frame_count:{type(frame_count)}\n'+
#                     f'self.bits_per_px:{type(self.bits_per_px)}\n'+
#                     f'index_mode:{type(index_mode)}\n'+
#                     f'offset_flag:{type(offset_flag)}\n'+
#                     f'size_x:{type(self.size[0])}\n'+
#                     f'size_y:{type(self.size[1])}\n'+
#                     f'hs_x:{type(hs_x)}:{hs_x}\n'+
#                     f'hs_y:{type(hs_y)}:{hs_y}\n'+
#                     f'self.bounding_box[0]:{type(self.bounding_box[0])}\n'+
#                     f'self.bounding_box[1]:{type(self.bounding_box[1])}\n'+
#                     f'self.bounding_box[2]:{type(self.bounding_box[2])}\n'+
#                     f'self.bounding_box[3]:{type(self.bounding_box[3])}\n'+
#                     f'palette_offset:{type(palette_offset)}\n'
#                     )
# =============================================================================
        #print(out_text)
        
        hedr_buf = struct.pack('I12HIII',
                               version,
                               frame_count,
                               self.bits_per_px,
                               index_mode,
                               offset_flag,
                               size_x,
                               size_y,
                               hs_x,
                               hs_y,
                               self.bounding_box[0],
                               self.bounding_box[1],
                               self.bounding_box[2],
                               self.bounding_box[3],
                               0,
                               0,
                               palette_offset)
        
        hedr_buf += frame_sizes + animations
               
        #print(f'chunk_name:{chunk_name}:{type(chunk_name)}\nchunk_length:{chunk_length}:{type(chunk_length)}')
        return struct.pack('>4sI', chunk_name, len(hedr_buf)) + hedr_buf + frame_buffer
        
    def encodeForm(self, file_buffer: bytes):
        chunk_name = b'FORM'
        length = len(file_buffer)
        file_type = b'TGAR'
        return struct.pack('>4sI4s', chunk_name, length, file_type) + file_buffer
    
    # Resizes input image to portrait dimensions
    def resize(self, portrait_size):
        inW, inH = self.imgs[0].size
        if portrait_size == "small":     # rescale to 66 X 72 (internal size of portrait frame)
            outW, outH, frame_width = 66, 72, 4
        elif portrait_size == "large":   # rescale to 220 X 220 (internal size of large frame)
            outW, outH, frame_width = 220, 220, 5
        else:
            print(f'{out_size} is not a valid size')
            sys.exit()
        
        if inH < (inW * outH / outW):   # if height less than width * inverse scaling factor
            crW = int(inH*outW/outH)    # set width equal to height, then scale to maintain AR
            crop = int((inW - crW) / 2)
            box = (crop ,0 ,inW - crop , inH)
            print(f'Cropping width from {inW} to {crW} using bounding box {box}')
        else:
            crH = int(inW*outH/outW)
            crop = int((inH - crH) / 2)
            box = (0 ,crop ,inW, inH - crop)
            print(f'Cropping height from {inH} to {crH} using bounding box {box}')
        
        print(f'Resizing to {outW}x{outH}')
        cropped_im = self.imgs[0].crop(box).resize((outW, outH))
        
        padding_im = Image.new('RGBA', (outW+2*frame_width,outH+2*frame_width))
        padding_im.paste(cropped_im, (frame_width,frame_width))
        
        self.imgs[0] = padding_im
        return
       
    def addPortraitFrame(self, portrait_size):
        frame = Image.open(resource_path(f'data/{portrait_size}-portrait-frame.png'))
        self.imgs[0].paste(frame, mask=frame)
        if portrait_size == 'large':
            # dtype=int16 allows for negatives rather than overflow
            hsv_im_data = np.array(self.imgs[0].convert('HSV'),dtype='int16')
            mask_img = np.array(Image.open(resource_path('data/large-portrait-shadow-mask.png')))
            # Shadow intensity 1 is represented with yellow pixels
            mask_s1 = ((mask_img[:,:,:3] == [255,255,0]).all(axis=2)*(5*2.55)).astype('uint8')
            # Shadow intensity 2 is represented with red pixels
            mask_s2 = ((mask_img[:,:,:3] == [255,0,0]).all(axis=2)*(10*2.55)).astype('uint8')
            # Shadow intensity 3 is represented with cyan pixels
            mask_s3 = ((mask_img[:,:,:3] == [0,255,255]).all(axis=2)*(20*2.55)).astype('uint8')
            # Shadow intensity 4 is represented with green pixels
            mask_s4 = ((mask_img[:,:,:3] == [0,255,0]).all(axis=2)*(30*2.55)).astype('uint8')
            # Shadow intensity 5 is represented with blue pixels
            mask_s5 = ((mask_img[:,:,:3] == [0,0,255]).all(axis=2)*(40*2.55)).astype('uint8')
            
            full_mask = (mask_s1 + mask_s2 + mask_s3 + mask_s4 + mask_s5)
            # Subtracts shadows from V channel
            hsv_im_data[:,:,2] -= full_mask
            # sets a minimum V of 6/255 ONLY for pixels in mask (true blacks elsewhere not affected)
            hsv_im_data[:,:,2][full_mask.astype('?')] = hsv_im_data[:,:,2][full_mask.astype('?')].clip(min=6)
            self.imgs[0] = Image.fromarray(hsv_im_data.astype('uint8'),mode='HSV').convert('RGBA')
        return
    

        
if __name__ == "__main__":
    pass
