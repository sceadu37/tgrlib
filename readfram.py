#!/usr/bin/pypy3

import sys
import tgrlib
from PIL import Image
from pathlib import Path

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Provide a file to unpack")
        exit()
    image_path = sys.argv[1]

    imagefile = tgrlib.tgrFile(image_path, False)

    imagefile.load()

    image_name = Path(image_path).stem

    #Path(image_name).mkdir(exist_ok=True)

    frame_index = 0
    pixel_format = "RGB"

    for frame_index, frame in enumerate([imagefile.frames[0]]):

    #print(imagefile.framecount)
    # frame = imagefile.frames[frame_index]

        print(frame.size)
        image = Image.new(pixel_format, frame.size)
        imagedata = b""
        with open(image_path, "rb") as in_fh:
            for idx in range(len(frame.lines)):
                rawline = imagefile.extractLine(in_fh, frame_index=frame_index, line_index=idx, increment=0)
                #print(f"{idx+1:3d}: 0x{frame.lines[idx].offset:06x}, {len(rawline)}")
                while len(rawline) < frame.size[0]:
                    rawline.append(tgrlib.Pixel(0, 0, 0))
                if len(rawline) > frame.size[0]:
                    rawline = rawline[0:frame.size[0]]
                imagedata += b"".join([elem.pack_to_bin(pixel_format) for elem in rawline])
                #print(len(imagedata))
        image.frombytes(imagedata)
        # image.save(f"{image_name}/fram_{frame_index}.png")

        image.save(f"{image_name}.png")
