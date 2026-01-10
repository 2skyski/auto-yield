
import ezdxf
from shapely.geometry import LineString, Polygon, Point
from shapely.ops import polygonize, linemerge
import math

def extract_lines(entity, lines_list):
    dxftype = entity.dxftype()
    try:
        if dxftype == 'LINE':
            start, end = entity.dxf.start, entity.dxf.end
            lines_list.append(LineString([(start.x, start.y), (end.x, end.y)]))
        elif dxftype in ['LWPOLYLINE', 'POLYLINE']:
            points = list(entity.points())
            if len(points) > 1:
                lines_list.append(LineString([(p[0], p[1]) for p in points]))
        elif dxftype in ['SPLINE', 'ARC', 'CIRCLE', 'ELLIPSE']:
            path = ezdxf.path.make_path(entity)
            vertices = list(path.flattening(distance=1.0))
            if len(vertices) > 1:
                lines_list.append(LineString([(v.x, v.y) for v in vertices]))
        elif dxftype == 'INSERT':
            for virtual_entity in entity.virtual_entities():
                extract_lines(virtual_entity, lines_list)
    except Exception:
        pass

def process_debug(file_path):
    print(f"DEBUGGING: {file_path}")
    doc = ezdxf.readfile(file_path)
    msp = doc.modelspace()
    
    layer_counts = {}
    lines = []
    
    for e in msp:
        layer = e.dxf.layer
        layer_counts[layer] = layer_counts.get(layer, 0) + 1
        extract_lines(e, lines)
        
    print(f"Entities per Layer: {layer_counts}")
    print(f"1. Extracted {len(lines)} raw lines.")

    # 1. Rounding (0.1mm)
    rounded_lines = []
    for line in lines:
        coords = list(line.coords)
        rounded_coords = [(round(x, 1), round(y, 1)) for x, y in coords]
        rounded_lines.append(LineString(rounded_coords))
    
    # 2. Merge
    merged_lines = linemerge(rounded_lines)
    
    # 3. Polygonize & Force Close
    raw_polys = list(polygonize(merged_lines))
    print(f"2. Initially formed {len(raw_polys)} polygons.")
    
    # Force Close Logic
    if hasattr(merged_lines, 'geoms'):
        chains = list(merged_lines.geoms)
    else:
        chains = [merged_lines] if merged_lines else []
        
    closed_count = 0
    for chain in chains:
        if not chain.is_ring:
            start_pt = Point(chain.coords[0])
            end_pt = Point(chain.coords[-1])
            gap = start_pt.distance(end_pt)
            
            if gap < 10.0:
                print(f"   -> Force Closing chain with gap {gap:.2f}")
                closed_coords = list(chain.coords) + [chain.coords[0]]
                new_poly = Polygon(closed_coords)
                if new_poly.is_valid and new_poly.area > 0:
                    raw_polys.append(new_poly)
                    closed_count += 1
    
    print(f"3. Force Closed {closed_count} additional polygons.")
    print(f"   Total Polygons: {len(raw_polys)}")
    
    # 4. Filtering (Size Filter)
    candidates = [p for p in raw_polys if (p.area/100) > 50]
    print(f"4. Candidates (Size Filtered): {len(candidates)}")
    
    # Group by Centroid
    groups = []
    processed_indices = set()
    
    for i, p in enumerate(candidates):
        if i in processed_indices: continue
        
        # New Group
        current_group = {'polys': [p], 'indices': [i]}
        processed_indices.add(i)
        
        for j, other in enumerate(candidates):
            if i == j or j in processed_indices: continue
            
            if p.centroid.distance(other.centroid) < 50: # Same center
                current_group['polys'].append(other)
                current_group['indices'].append(j)
                processed_indices.add(j)
        
        groups.append(current_group)
        
    print(f"5. Found {len(groups)} Pattern Groups (Concentric Polygons):")
    for gidx, grp in enumerate(groups):
        areas = [p.area for p in grp['polys']]
        areas.sort(reverse=True)
        print(f"   - Group {gidx+1}: Found {len(areas)} polys. Areas: {['{:.1f}'.format(a) for a in areas]}")
        if len(areas) > 1:
            ratio = areas[0] / areas[1]
            print(f"     -> Outer/Inner Ratio: {ratio:.4f}")
            if ratio < 1.10:
                print(f"     -> Likely Cut Line & Sew Line pair.")
            else:
                print(f"     -> Large difference. Maybe something else.")
        else:
            print(f"     -> ONLY ONE POLYGON FOUND (Missing Cut Line?)")

process_debug('35717요척.dxf')
