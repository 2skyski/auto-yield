
import ezdxf
from shapely.geometry import LineString, Polygon
from shapely.ops import polygonize

def analyze_forensics(file_path):
    print(f"Forensic Analysis of {file_path}...")
    doc = ezdxf.readfile(file_path)
    
    # 1. TEXT Content Analysis
    print("\n[1] TEXT ENTITIES:")
    texts = []
    for e in doc.modelspace().query('TEXT MTEXT'):
        text_val = e.dxf.text if e.dxftype() == 'TEXT' else e.text
        print(f" - [{e.dxftype()}] Layer='{e.dxf.layer}': '{text_val}'")
        texts.append(text_val)
        
    # 2. Block Definition Analysis (Geometry Count)
    print("\n[2] BLOCK DEFINITIONS:")
    total_block_length = 0
    block_geometries = {}
    
    for block in doc.blocks:
        if block.name.startswith('*'): continue # Skip anonymous/layout blocks
        # if block.is_layout_block: continue # Removed due to attribute error in older ezdxf
        
        linelen = 0
        count = 0
        for e in block:
            dxftype = e.dxftype()
            l = 0
            if dxftype == 'LINE':
                start, end = e.dxf.start, e.dxf.end
                l = start.distance(end)
            elif dxftype in ['LWPOLYLINE', 'POLYLINE']:
                # Approximate length
                pts = list(e.points())
                if len(pts) > 1:
                    for i in range(len(pts)-1):
                        p1, p2 = pts[i], pts[i+1]
                        # simple euclidian dist
                        l += ((p1[0]-p2[0])**2 + (p1[1]-p2[1])**2)**0.5
            
            if l > 0:
                linelen += l
                count += 1
                
        if count > 0:
            print(f" - Block '{block.name}': {count} entities, Total Length={linelen:.1f}")
            block_geometries[block.name] = linelen
            
    # 3. Modelspace Instantiation
    print("\n[3] MODELSPACE INSTANCES:")
    total_ms_length_calc = 0
    msp = doc.modelspace()
    for e in msp:
        if e.dxftype() == 'INSERT':
            bname = e.dxf.name
            if bname in block_geometries:
                # Add block length (ignoring scaling for now as usually 1.0)
                total_ms_length_calc += block_geometries[bname]
        elif e.dxftype() in ['LINE', 'LWPOLYLINE', 'POLYLINE']:
            # Add direct length
            pass # Simplified for now
            
    print(f"  -> Sum of Block Geometries in MS: {total_ms_length_calc:.1f}")

    # 4. Compare with Detected Polygon Perimeters
    # We need to run the extraction logic (simplified) to get detected perimeters
    print("\n[4] DETECTED POLYGONS PERIMETER:")
    # ... (Re-using simplified extract/polygonize logic) ...
    # For speed, let's just use the `debug_check_logic` result if available, 
    # but here I'll just do a quick heuristic based on previous runs.
    # User had 13 patterns. Let's assume the previous debug output of ~13 polygons is correct.
    # Previous output showed Area ~247000. 
    # Square root of 247000 is ~500mm side. Perimeter ~2000mm per pattern.
    # 13 patterns * 2000 = ~26000mm total perimeter.
    
    # If Total Line Length is ~26000, then ONLY Sew Lines exist.
    # If Total Line Length is ~52000, then Cut Lines must be there.
    
    # Let's perform a concrete extraction to be sure.
    raw_lines = []
    def extract(entity):
        if entity.dxftype() == 'INSERT':
            for v in entity.virtual_entities(): extract(v)
        elif entity.dxftype() == 'LINE':
            raw_lines.append(entity.dxf.start.distance(entity.dxf.end))
        elif entity.dxftype() == 'LWPOLYLINE':
            pts = list(entity.points())
            l = 0
            for i in range(len(pts)-1):
                l += ((pts[i][0]-pts[i+1][0])**2 + (pts[i][1]-pts[i+1][1])**2)**0.5
            raw_lines.append(l)
            
    for e in msp: extract(e)
    
    total_physical_length = sum(raw_lines)
    print(f"  -> Total Physical Length of ALL Lines: {total_physical_length:.1f}")

analyze_forensics('35717요척.dxf')
