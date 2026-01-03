import bpy
from bpy.props import StringProperty, FloatProperty, EnumProperty, BoolProperty
from bpy_extras.io_utils import ExportHelper


class LEVELDESIGN_OT_export_glb_scaled(bpy.types.Operator, ExportHelper):
    """Export scene to GLB with custom scale applied to geometry"""
    bl_idname = "leveldesign.export_glb_scaled"
    bl_label = "Export GLB (Scaled)"
    bl_options = {'PRESET'}

    filename_ext = ".glb"

    filter_glob: StringProperty(
        default="*.glb;*.gltf",
        options={'HIDDEN'},
    )

    export_scale: FloatProperty(
        name="Scale",
        description="Scale factor to apply to exported geometry",
        default=1.0,
        min=0.001,
        max=1000.0,
        soft_min=0.01,
        soft_max=100.0,
    )

    export_format: EnumProperty(
        name="Format",
        items=[
            ('GLB', "GLB (.glb)", "Export as single binary file"),
            ('GLTF_SEPARATE', "GLTF + Bin + Textures", "Export as separate files"),
            ('GLTF_EMBEDDED', "GLTF Embedded (.gltf)", "Export as single JSON file with embedded data"),
        ],
        default='GLB',
    )

    export_textures: BoolProperty(
        name="Export Textures",
        description="Include textures in the export",
        default=True,
    )

    export_normals: BoolProperty(
        name="Export Normals",
        description="Include vertex normals in the export",
        default=True,
    )

    export_apply_modifiers: BoolProperty(
        name="Apply Modifiers",
        description="Apply modifiers before exporting",
        default=True,
    )

    def execute(self, context):
        import bmesh

        # Get all mesh and curve objects to export
        original_objects = [obj for obj in bpy.data.objects if obj.type in ('MESH', 'CURVE')]

        if not original_objects:
            self.report({'WARNING'}, "No mesh or curve objects to export")
            return {'CANCELLED'}

        # Store original selection and active object
        original_selection = context.selected_objects.copy()
        original_active = context.view_layer.objects.active

        # Create temporary duplicates for export
        temp_objects = []

        # Deselect all using low-level API (works in any context)
        for obj in bpy.data.objects:
            obj.select_set(False)

        # Mapping from original to duplicate for modifier remapping
        obj_mapping = {}

        # First pass: duplicate all objects (without scaling yet)
        for obj in original_objects:
            new_obj = obj.copy()
            new_obj.data = obj.data.copy()
            context.collection.objects.link(new_obj)
            obj_mapping[obj] = new_obj
            temp_objects.append(new_obj)

        # Remap modifier targets to duplicated objects
        for new_obj in temp_objects:
            for mod in new_obj.modifiers:
                if hasattr(mod, 'object') and mod.object in obj_mapping:
                    mod.object = obj_mapping[mod.object]
                if hasattr(mod, 'target') and mod.target in obj_mapping:
                    mod.target = obj_mapping[mod.target]
                if hasattr(mod, 'mirror_object') and mod.mirror_object in obj_mapping:
                    mod.mirror_object = obj_mapping[mod.mirror_object]

        # Update view layer so depsgraph sees the remapped modifiers
        context.view_layer.update()

        # Apply modifiers to mesh objects using operator (more reliable for complex modifiers)
        if self.export_apply_modifiers:
            for new_obj in temp_objects:
                if new_obj.type == 'MESH' and new_obj.modifiers:
                    # Select only this object
                    for obj in bpy.data.objects:
                        obj.select_set(False)
                    new_obj.select_set(True)
                    context.view_layer.objects.active = new_obj

                    # Apply all modifiers
                    while new_obj.modifiers:
                        try:
                            bpy.ops.object.modifier_apply(modifier=new_obj.modifiers[0].name)
                        except RuntimeError:
                            # Some modifiers can't be applied, remove them
                            new_obj.modifiers.remove(new_obj.modifiers[0])

        # Second pass: scale all geometry
        for orig_obj, new_obj in obj_mapping.items():
            # Scale location relative to world origin
            new_obj.location = orig_obj.location * self.export_scale

            # Combined scale factor
            scale_vec = orig_obj.scale * self.export_scale

            if new_obj.type == 'MESH':
                # Scale the mesh data
                mesh = new_obj.data
                bm = bmesh.new()
                bm.from_mesh(mesh)
                bmesh.ops.scale(bm, vec=scale_vec, verts=bm.verts)
                bm.to_mesh(mesh)
                bm.free()
                mesh.update()

            elif new_obj.type == 'CURVE':
                # Scale curve data
                curve = new_obj.data
                for spline in curve.splines:
                    if spline.type == 'BEZIER':
                        for point in spline.bezier_points:
                            point.co.x *= scale_vec.x
                            point.co.y *= scale_vec.y
                            point.co.z *= scale_vec.z
                            point.handle_left.x *= scale_vec.x
                            point.handle_left.y *= scale_vec.y
                            point.handle_left.z *= scale_vec.z
                            point.handle_right.x *= scale_vec.x
                            point.handle_right.y *= scale_vec.y
                            point.handle_right.z *= scale_vec.z
                    else:  # NURBS or POLY
                        for point in spline.points:
                            point.co.x *= scale_vec.x
                            point.co.y *= scale_vec.y
                            point.co.z *= scale_vec.z

            # Reset scale to 1 since we baked it into the data
            new_obj.scale = (1.0, 1.0, 1.0)

        # Select temp objects
        for obj in temp_objects:
            obj.select_set(True)
        context.view_layer.objects.active = temp_objects[0] if temp_objects else None

        try:
            # Hide original objects temporarily
            for obj in original_objects:
                obj.hide_set(True)

            # Export using Blender's built-in GLTF exporter
            # Note: export_apply=False because we already applied modifiers ourselves
            # (needed to apply before scaling for correct results with deform modifiers)
            bpy.ops.export_scene.gltf(
                filepath=self.filepath,
                export_format=self.export_format,
                export_texcoords=True,
                export_normals=self.export_normals,
                export_materials='EXPORT' if self.export_textures else 'NONE',
                export_apply=False,
                use_selection=False,
                use_visible=True,
            )

            self.report({'INFO'}, f"Exported to {self.filepath} with scale {self.export_scale}")

            # Save last export settings
            props = context.scene.level_design_props
            props.last_export_filepath = self.filepath
            props.last_export_scale = self.export_scale
            props.last_export_format = self.export_format
            props.last_export_textures = self.export_textures
            props.last_export_normals = self.export_normals
            props.last_export_apply_modifiers = self.export_apply_modifiers

        finally:
            # Unhide original objects
            for obj in original_objects:
                obj.hide_set(False)

            # Delete temporary objects using low-level API
            for obj in temp_objects:
                data = obj.data
                data_type = obj.type
                bpy.data.objects.remove(obj)
                # Remove orphaned data
                if data and data.users == 0:
                    if data_type == 'MESH':
                        bpy.data.meshes.remove(data)
                    elif data_type == 'CURVE':
                        bpy.data.curves.remove(data)

            # Restore original selection using low-level API
            for obj in bpy.data.objects:
                obj.select_set(False)
            for obj in original_selection:
                if obj.name in bpy.data.objects:
                    obj.select_set(True)
            if original_active and original_active.name in bpy.data.objects:
                context.view_layer.objects.active = original_active

        return {'FINISHED'}

    def draw(self, context):
        layout = self.layout
        layout.use_property_split = True
        layout.use_property_decorate = False

        layout.prop(self, "export_scale")
        layout.separator()
        layout.prop(self, "export_format")
        layout.prop(self, "export_textures")
        layout.prop(self, "export_normals")
        layout.prop(self, "export_apply_modifiers")


class LEVELDESIGN_OT_export_glb_quick(bpy.types.Operator):
    """Export scene to GLB using last export settings (no dialog)"""
    bl_idname = "leveldesign.export_glb_quick"
    bl_label = "Export Scaled GLB using Last Settings"

    @classmethod
    def poll(cls, context):
        props = context.scene.level_design_props
        return props.last_export_filepath != ""

    def execute(self, context):
        props = context.scene.level_design_props

        if not props.last_export_filepath:
            self.report({'ERROR'}, "No previous export path. Use File > Export > GLB Scaled first.")
            return {'CANCELLED'}

        # Call the existing export operator with saved settings
        return bpy.ops.leveldesign.export_glb_scaled(
            filepath=props.last_export_filepath,
            export_scale=props.last_export_scale,
            export_format=props.last_export_format,
            export_textures=props.last_export_textures,
            export_normals=props.last_export_normals,
            export_apply_modifiers=props.last_export_apply_modifiers,
        )


def menu_func_export(self, context):
    self.layout.operator(LEVELDESIGN_OT_export_glb_scaled.bl_idname, text="GLB Scaled (.glb)")


def register():
    bpy.utils.register_class(LEVELDESIGN_OT_export_glb_scaled)
    bpy.utils.register_class(LEVELDESIGN_OT_export_glb_quick)
    bpy.types.TOPBAR_MT_file_export.append(menu_func_export)


def unregister():
    bpy.types.TOPBAR_MT_file_export.remove(menu_func_export)
    bpy.utils.unregister_class(LEVELDESIGN_OT_export_glb_quick)
    bpy.utils.unregister_class(LEVELDESIGN_OT_export_glb_scaled)
