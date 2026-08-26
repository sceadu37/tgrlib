import tgrlib
from pathlib import Path

walk_dir = Path("C:/Users/Elijah/Documents/KAG/ExtractedGameFiles/Kohan_ag/ART/OBJECTS")
out_path = Path("C:/Users/Elijah/Documents/KAG/sprite_types.csv")

# Source - https://stackoverflow.com/a/2212698
# Posted by AndiDog, modified by community. See post 'Timeline' for change history
# Retrieved 2026-08-25, License - CC BY-SA 3.0

import os

print('walk_dir = ', walk_dir)

# If your current working directory may change during script execution, it's recommended to
# immediately convert program arguments to an absolute path. Then the variable root below will
# be an absolute path as well. Example:
# walk_dir = os.path.abspath(walk_dir)
print('walk_dir (absolute) = ', walk_dir.resolve())
with open(out_path, "w") as f_out:
    for root, subdirs, files in walk_dir.walk():
        print('--\nroot = ', root)
        for subdir in subdirs:
            print('\t- subdirectory ' + subdir)

        for filename in files:
            file_path = root.joinpath(filename)

            print('\t- file %s (full path: %s)' % (filename, file_path))
            tgr = tgrlib.tgrFile(file_path)
            tgr.load()
            data = [
                filename,
                file_path.parent,
                tgr.bits_per_px,
                tgr.mode_1,
                tgr.mode_2,
                tgr.offset_flag,
                *tgr.unknown_shorts,]
            f_out.write(",".join(data))




