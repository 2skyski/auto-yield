
import ezdxf
from shapely.geometry import LineString, Polygon
from shapely.ops import polygonize, linemerge, unary_union
import matplotlib.pyplot as plt

def analyze_dxf(file_path):
    print(f"Analyzing {file_path}...")
    doc = ezdxf.readfile(file_path)
    msp = doc.modelspace()
    
    raw_lines = []
    
    # 1. Extract Raw Entities
    for e in msp:
        try:
            if e.dxftype() == 'LINE':
                raw_lines.append(LineString([e.dxf.start[:2], e.dxf.end[:2]]))
            elif e.dxftype() == 'LWPOLYLINE':
                pts = list(e.points())
                if len(pts) > 1:
                    raw_lines.append(LineString([(p[0], p[1]) for p in pts]))
        except: pass
        
    print(f"Extracted {len(raw_lines)} raw line segments.")
    
    # 2. Try Polygonize DIRECTLY
    polys_direct = list(polygonize(raw_lines))
    print(f"Direct Polygonize found {len(polys_direct)} polygons.")
    if polys_direct:
        print(f"Largest Direct Poly Area: {max([p.area for p in polys_direct]):.2f}")
    
    # 3. Try Merge then Polygonize
    merged = linemerge(raw_lines)
    if not isinstance(merged, list): merged = [merged] # Handle single result
    print(f"LineMerge produced {len(merged)} chains.")
    
    # Check for closed rings in merged lines
    closed_chains = 0
    open_chains = 0
    chain_lengths = []
    
    for geom in merged:
        if geom.is_ring:
            closed_chains += 1
        else:
            open_chains += 1
            chain_lengths.append(geom.length)
            
    print(f"Merged Chains: {closed_chains} Closed, {open_chains} Open.")
    if open_chains > 0:
        print(f"Top 3 Longest Open Chains: {sorted(chain_lengths, reverse=True)[:3]}")
        
    # 4. Try Rounding
    rounded_lines = []
    for line in raw_lines:
        coords = list(line.coords)
        rounded_coords = [(round(x, 3), round(y, 3)) for x, y in coords]
        rounded_lines.append(LineString(rounded_coords))
        
    merged_rounded = linemerge(rounded_lines)
    polys_rounded = list(polygonize(merged_rounded))
    print(f"Rounded Polygonize found {len(polys_rounded)} polygons.")
    if polys_rounded:
        print(f"Largest Rounded Poly Area: {max([p.area for p in polys_rounded]):.2f}")

analyze_dxf('35717요척.dxf')
