
import ezdxf

def inspect_xdata(file_path):
    print(f"Inspecting XDATA in {file_path}...")
    doc = ezdxf.readfile(file_path)
    msp = doc.modelspace()
    
    xdata_counts = {}
    examples = []
    
    for e in msp:
        # Low-level XDATA check via tags
        if hasattr(e, 'tags'):
            current_appid = None
            for tag in e.tags:
                if tag.code == 1001: # APPID
                    current_appid = tag.value
                    xdata_counts[current_appid] = xdata_counts.get(current_appid, 0) + 1
                elif tag.code >= 1000 and current_appid:
                    if len(examples) < 10:
                        examples.append((e.dxftype(), current_appid, (tag.code, tag.value)))



                    
        # Check for Extended Entity Data (Extension Dictionaries) which some CADs use
        if e.has_extension_dict:
            xdata_counts['EXTENSION_DICT'] = xdata_counts.get('EXTENSION_DICT', 0) + 1

    print(f"XDATA Counts by AppID: {xdata_counts}")
    print("\n--- XDATA Examples ---")
    for dxftype, appid, data in examples:
        print(f"Type: {dxftype}, AppID: {appid}")
        for code, value in data:
            print(f"  Code {code}: {value}")

inspect_xdata('35717요척.dxf')
