
import ezdxf
from collections import Counter

def analyze_dxf_structure(file_path):
    print(f"Analyzing Structure of {file_path}...")
    doc = ezdxf.readfile(file_path)
    msp = doc.modelspace()
    
    types = []
    for e in msp:
        types.append(e.dxftype())
        
    print(f"Modelspace Entities: {Counter(types)}")
    
    # Check Blocks if INSERTs exist
    if 'INSERT' in types:
        print("\nChecking Blocks referenced by INSERTs:")
        for e in msp:
            if e.dxftype() == 'INSERT':
                print(f" - Inserted Block Name: {e.dxf.name}")
                # Analyze content of this block
                if e.dxf.name in doc.blocks:
                    blk = doc.blocks[e.dxf.name]
                    blk_types = [be.dxftype() for be in blk]
                    print(f"   -> Contains: {Counter(blk_types)}")

analyze_dxf_structure('35717요척.dxf')
