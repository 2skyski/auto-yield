
import ezdxf
from shapely.geometry import LineString, Point
from shapely.ops import linemerge, polygonize

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

def test_rounding(file_path):
    print(f"Testing Rounding on {file_path}...")
    doc = ezdxf.readfile(file_path)
    msp = doc.modelspace()
    
    raw_lines = []
    for e in msp:
        extract_recursive(e, raw_lines)
    
    print(f"Total Segments: {len(raw_lines)}")
    
    for decimals in [3, 2, 1, 0]:
        print(f"\n--- Rounding to {decimals} decimals ---")
        rounded = []
        for line in raw_lines:
            coords = [(round(x, decimals), round(y, decimals)) for x, y in line.coords]
            rounded.append(LineString(coords))
            
        merged = linemerge(rounded)
        # Handle GeometryCollection or MultiLineString
        chains = []
        if hasattr(merged, 'geoms'):
            chains = list(merged.geoms)
        else:
            chains = [merged]
            
        polys = list(polygonize(chains))
        print(f"Polygons Found: {len(polys)}")
        if len(polys) > 0:
            areas = sorted([p.area for p in polys], reverse=True)
            print(f"Top 3 Areas: {areas[:3]}")
        else:
            # Check open chains
            print(f"Open Chains: {len(chains)}")
            if len(chains) > 0:
                 print(f"First Chain Length: {chains[0].length:.2f}")

test_rounding('35717요척.dxf')
