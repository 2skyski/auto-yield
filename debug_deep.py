
import ezdxf
from shapely.geometry import LineString, Point
from shapely.ops import linemerge

def extract_recursive(entity, collector):
    dxftype = entity.dxftype()
    try:
        if dxftype == 'INSERT':
            for virtual in entity.virtual_entities():
                extract_recursive(virtual, collector)
        elif dxftype == 'LINE':
            collector.append(LineString([entity.dxf.start[:2], entity.dxf.end[:2]]))
        elif dxftype in ['LWPOLYLINE', 'POLYLINE']:
            points = list(entity.points())
            if len(points) > 1:
                collector.append(LineString([(p[0], p[1]) for p in points]))
    except: pass

def analyze_geometry(file_path):
    print(f"Deep Analysis of {file_path}...")
    doc = ezdxf.readfile(file_path)
    msp = doc.modelspace()
    
    all_lines = []
    for e in msp:
        extract_recursive(e, all_lines)
        
    print(f"Total Line Segments Extracted: {len(all_lines)}")
    
    # Analyze Merged Chains
    merged = linemerge(all_lines)
    if not isinstance(merged, list): merged = [merged] # Handle single
    if hasattr(merged, 'geoms'): merged = list(merged.geoms) # Handle MultiLineString
    
    print(f"Merged into {len(merged)} continuous chains.")
    
    for i, geom in enumerate(merged):
        start = Point(geom.coords[0])
        end = Point(geom.coords[-1])
        dist = start.distance(end)
        is_closed = dist < 0.001
        
        print(f"Chain {i}: Length={geom.length:.2f}, Closed={is_closed} (Gap={dist:.5f})")
        
        # If open but gap is small-ish, it's a candidate for the Cut Line
        if not is_closed and dist < 2.0:
            print(f"   -> WARNING: Found Open Chain with small gap ({dist:.5f}). This is likely the missing Cut Line.")

analyze_geometry('35717요척.dxf')
