import bpy

def safe_get_input(node, name):
    """Helper to safely access node sockets."""
    return node.inputs.get(name) or node.outputs.get(name)

def get_or_create_campus_material():
    """Creates or retrieves the Campus_Material."""
    mat_name = "Campus_Material"
    mat = bpy.data.materials.get(mat_name)
    if not mat:
        mat = bpy.data.materials.new(name=mat_name)
        mat.use_nodes = True
        nodes = mat.node_tree.nodes
        nodes.clear()
        
        # Create Principled BSDF
        bsdf = nodes.new(type='ShaderNodeBsdfPrincipled')
        bsdf.location = (0, 0)
        
        # Create Output
        output = nodes.new(type='ShaderNodeOutputMaterial')
        output.location = (300, 0)
        
        # Link
        mat.node_tree.links.new(bsdf.outputs['BSDF'], output.inputs['Surface'])
        
        # Configure
        base_color = safe_get_input(bsdf, "Base Color")
        if base_color:
            base_color.default_value = (0.8, 0.7, 0.6, 1.0)
            
    return mat

def assign_material_to_objects(obj_names):
    """Assigns the Campus_Material to a list of object names."""
    mat = get_or_create_campus_material()
    for name in obj_names:
        obj = bpy.data.objects.get(name)
        if obj and obj.type == 'MESH':
            if obj.data.materials:
                obj.data.materials[0] = mat
            else:
                obj.data.materials.append(mat)

# Execute assignment for known campus objects
if __name__ == "__main__":
    target_objects = ["Campus_Building_Base", "Campus_Wing_Left", "Campus_Wing_Right", "Campus_Roof"]
    assign_material_to_objects(target_objects)
