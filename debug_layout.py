
import ezdxf

def analyze_layouts(file_path):
    print(f"Deep Scan of {file_path}...")
    doc = ezdxf.readfile(file_path)
    
    # 1. Check Limits/Extents
    print(f"Header EXTMIN: {doc.header.get('$EXTMIN', 'N/A')}")
    print(f"Header EXTMAX: {doc.header.get('$EXTMAX', 'N/A')}")
    
    # 2. Iterate ALL Layouts (Modelspace + Paperspace)
    print("\n--- Layouts Analysis ---")
    for layout_name in doc.layout_names():
        layout = doc.layout(layout_name)
        count = len(layout)
        print(f"Layout '{layout_name}': {count} entities")
        
        # Sample types
        types = {}
        for e in layout:
            types[e.dxftype()] = types.get(e.dxftype(), 0) + 1
        print(f"   -> Entities: {types}")
        
    # 3. Layer Status
    print("\n--- Layers Analysis ---")
    for layer in doc.layers:
        print(f"Layer '{layer.dxf.name}': On={layer.is_on()}, Frozen={layer.is_frozen()}, Locked={layer.is_locked()}")

analyze_layouts('35717요척.dxf')
