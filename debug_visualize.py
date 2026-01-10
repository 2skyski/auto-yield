
import ezdxf
import matplotlib.pyplot as plt
from shapely.geometry import LineString, Polygon
from shapely.ops import linemerge

def extract_all_raw(entity, collector):
    dxftype = entity.dxftype()
    try:
        if dxftype == 'LINE':
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
        elif dxftype == 'INSERT':
            for virtual in entity.virtual_entities():
                extract_all_raw(virtual, collector)
    except: pass

def visualize_dxf(file_path):
    doc = ezdxf.readfile(file_path)
    msp = doc.modelspace()
    
    lines = []
    for e in msp: extract_all_raw(e, lines)
    
    print(f"Extracted {len(lines)} lines for visualization.")
    
    fig, ax = plt.subplots(figsize=(10, 10))
    
    for geom in lines:
        x, y = geom.xy
        ax.plot(x, y, 'b-', linewidth=0.5, alpha=0.7)
        
    ax.set_aspect('equal')
    ax.set_title(f"Raw DXF Content: {len(lines)} segments")
    
    # Save to artifacts directory for user view
    output_path = r"C:\Users\sk.SYSTEM-HP\.gemini\antigravity\brain\7b49ce7c-4af1-4879-973a-a1e8bb0c4668\dxf_debug_view.png"
    fig.savefig(output_path, dpi=150)
    print(f"Saved visualization to {output_path}")

visualize_dxf('35717요척.dxf')
