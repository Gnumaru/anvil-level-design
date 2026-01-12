"""
Utility script to find the memory offset of uvcalc_flag in Blender's ToolSettings struct.

Run this in Blender's Python console to discover the offset for your Blender version.
The offset is needed for directly manipulating the UVCALC_TRANSFORM_CORRECT_SLIDE flag,
which controls whether edge/vertex slide operations correct UVs.

Usage:
    1. Open Blender's Python console (Scripting workspace)
    2. Copy and paste this entire script
    3. Run it
    4. Note the reported offset value
"""

import ctypes
import bpy


def find_uvcalc_flag_offset():
    """Find the offset of uvcalc_flag in ToolSettings by toggling a known flag.

    Uses use_transform_correct_face_attributes (UVCALC_TRANSFORM_CORRECT = bit 4, value 16)
    as a reference point since it's exposed to Python and shares the same uvcalc_flag field.
    """
    ts = bpy.context.scene.tool_settings
    ts_ptr = ts.as_pointer()

    # Get current state of the known flag
    original = ts.use_transform_correct_face_attributes

    # Find candidates where bit 4 (value 16) matches current state
    candidates = []
    for offset in range(0, 4000, 2):  # Scan as shorts (2 bytes)
        try:
            ptr = ctypes.cast(ts_ptr + offset, ctypes.POINTER(ctypes.c_ushort))
            val = ptr.contents.value
            has_bit = bool(val & 16)
            if has_bit == original:
                candidates.append((offset, val))
        except:
            pass

    # Toggle the flag
    ts.use_transform_correct_face_attributes = not original

    # Find which candidate changed
    for offset, old_val in candidates:
        try:
            ptr = ctypes.cast(ts_ptr + offset, ctypes.POINTER(ctypes.c_ushort))
            new_val = ptr.contents.value
            if new_val != old_val:
                # Verify the bit actually flipped
                old_bit = bool(old_val & 16)
                new_bit = bool(new_val & 16)
                if old_bit != new_bit:
                    print(f"FOUND: offset={offset}, old={old_val} ({bin(old_val)}), new={new_val} ({bin(new_val)})")
                    # Restore original
                    ts.use_transform_correct_face_attributes = original
                    return offset
        except:
            pass

    # Restore original
    ts.use_transform_correct_face_attributes = original
    print("Not found!")
    return None


if __name__ == "__main__":
    offset = find_uvcalc_flag_offset()
    if offset is not None:
        print(f"\nuvcalc_flag offset: {offset}")
        print(f"\nUpdate _UVCALC_FLAG_OFFSET in handlers.py to: {offset}")
