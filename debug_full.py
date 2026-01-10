
import ezdxf
from shapely.geometry import LineString, Point, Polygon
from shapely.ops import linemerge, polygonize
import math

def extract_full(entity, collector):
    dxftype = entity.dxftype()
    try:
        if dxftype == 'INSERT':
            for virtual in entity.virtual_entities():
                extract_full(virtual, collector)
        elif dxftype == 'LINE':
            collector.append(LineString([entity.dxf.start[:2], entity.dxf.end[:2]]))
        elif dxftype in ['LWPOLYLINE', 'POLYLINE']:
            points = list(entity.points())
            if len(points) > 1:
                collector.append(LineString([(p[0], p[1]) for p in points]))
        elif dxftype in ['SPLINE', 'ARC', 'CIRCLE', 'ELLIPSE']:
            path = ezdxf.path.make_path(entity)
            vertices = list(path.flattening(distance=1.0))
            if len(vertices) > 1:
                collector.append(LineString([(v.x, v.y) for v in vertices]))
    except: pass

def analyze_full(file_path):
    print(f"Full Analysis of {file_path}...")
    doc = ezdxf.readfile(file_path)
    msp = doc.modelspace()
    
    raw_lines = []
    for e in msp:
        extract_full(e, raw_lines)
    
    # 1. Rounding
    rounded = []
    for line in raw_lines:
        coords = [(round(x, 1), round(y, 1)) for x, y in line.coords] # Aggressive 1 decimal rounding
        rounded.append(LineString(coords))
        
    merged = linemerge(rounded)
    
    chains = []
    if hasattr(merged, 'geoms'):
        chains = list(merged.geoms)
    else:
        chains = [merged]
        
    closed_polys = []
    open_chains = []
    
    for geom in chains:
        if geom.is_ring:
            closed_polys.append(Polygon(geom))
        else:
            open_chains.append(geom)
            
    print(f"Closed Polygons: {len(closed_polys)}")
    if closed_polys:
        print(f"Max Area: {max([p.area for p in closed_polys]):.1f}")
        
    print(f"Open Chains: {len(open_chains)}")
    for i, c in enumerate(sorted(open_chains, key=lambda x: x.length, reverse=True)[:5]):
        start = Point(c.coords[0])
        end = Point(c.coords[-1])
        gap = start.distance(end)
        print(f" - Open Chain {i}: Length={c.length:.1f}, Gap={gap:.4f}")

analyze_full('35717요척.dxf')
