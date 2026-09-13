"""Build a portable, fully keyframed Blender film from every historical geometry state."""
from pathlib import Path
import contextlib, json, math, re, sys
import bpy
from mathutils import Vector

folder = Path(sys.argv[sys.argv.index('--') + 1]).resolve()
config = json.loads((folder / 'config.json').read_text(encoding='utf-8'))
timeline = json.loads((folder / 'timeline.json').read_text(encoding='utf-8'))
scene = bpy.context.scene
if bpy.app.version < (5, 0, 1):
    raise RuntimeError('Blender 5.0.1 or newer is required by the importer.')
bpy.ops.object.select_all(action='SELECT')
bpy.ops.object.delete(use_global=False)
# Load the isolated source checkout, without installing or changing user preferences.
import importlib.util
source = Path(config['importer_source'])
spec = importlib.util.spec_from_file_location('BesiegeCreationImporter', source / '__init__.py', submodule_search_locations=[str(source)])
addon = importlib.util.module_from_spec(spec)
sys.modules['BesiegeCreationImporter'] = addon
spec.loader.exec_module(addon)
addon.register()
skins = Path(config['besiege_data']) / 'Skins'
workshop = folder / 'unused_workshop'
workshop.mkdir(exist_ok=True)
api = addon.blenapi.BlenderAPI(str(workshop), str(skins), str(skins / 'Template'))
with (folder / 'import.log').open('w', encoding='utf-8') as log, contextlib.redirect_stdout(log):
    stats = api.ReadBSGData(str(folder / 'all_variants.bsg'))
    result = api.ImportCreation(vanilla_skins=True, create_parent=False, generate_material=True,
        merge_decor_blocks=True, join_line_components=True, line_type_cleanup='DELETE_EMPTIES',
        bracethreshold=0.05, use_node_groups=False, surface_block_thickness_mult=1.0)
print('IMPORT_STATS', stats, flush=True)
bpy.context.view_layer.update()
guid_re = re.compile(r'[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}')
groups = {v['guid']: [] for v in timeline['variants']}
geometry = []
depsgraph = bpy.context.evaluated_depsgraph_get()
for obj in list(scene.objects):
    if obj.type != 'MESH':
        continue
    match = guid_re.search(obj.name)
    if not match or match.group() not in groups:
        raise RuntimeError(f'Unmapped imported object: {obj.name}')
    guid = match.group()
    matrix = obj.matrix_world.copy()
    baked = bpy.data.meshes.new_from_object(obj.evaluated_get(depsgraph), depsgraph=depsgraph)
    obj.modifiers.clear()
    obj.constraints.clear()
    obj.parent = None
    obj.data = baked
    obj.matrix_world = matrix
    obj['history_variant_guid'] = guid
    groups[guid].append(obj)
    geometry.append(obj)
missing = [v for v in timeline['variants'] if not groups[v['guid']]]
assert not missing, f'Missing geometry: {missing}'
for obj in list(scene.objects):
    if obj.type == 'EMPTY': bpy.data.objects.remove(obj, do_unlink=True)
bpy.context.view_layer.update()

# Frame once against ALL historical extents; never translate or recenter any model state.
points = [obj.matrix_world @ Vector(c) for obj in geometry for c in obj.bound_box]
lo = Vector(tuple(min(p[i] for p in points) for i in range(3)))
hi = Vector(tuple(max(p[i] for p in points) for i in range(3)))
center = (lo + hi) / 2
size = max(max(hi - lo), .1)
rho = max(math.hypot(p.x - center.x, p.y - center.y) for p in points)
height = hi.z - lo.z
target = bpy.data.objects.new('FIXED_FOCUS_all_history_bounds', None)
scene.collection.objects.link(target)
target.location = center
rig = bpy.data.objects.new('CONTINUOUS_ORBIT_24_seconds_per_revolution', None)
scene.collection.objects.link(rig)
rig.location = center
camera_data = bpy.data.cameras.new('Product_50mm')
camera = bpy.data.objects.new('CAMERA', camera_data)
scene.collection.objects.link(camera)
scene.camera = camera
camera.parent = rig
camera_data.lens = 50
camera_data.sensor_fit = 'HORIZONTAL'
elevation = math.radians(17)
vfov = 2 * math.atan((36 * config['height'] / config['width']) / (2 * 50))
radius = max(rho * 1.12 + (height * math.cos(elevation) / 2 + rho * math.sin(elevation)) / (math.tan(vfov / 2) * .76), rho * 3.3)
camera.location = (radius, 0, radius * math.tan(elevation))
# Avoid the original 0.1..5000 depth range that triggered EEVEE shadow artifacts.
camera_data.clip_start = max(.01, camera.location.length * .05)
camera_data.clip_end = camera.location.length + size * 10
track = camera.constraints.new('TRACK_TO')
track.target = target
track.track_axis = 'TRACK_NEGATIVE_Z'
track.up_axis = 'UP_Y'
# Drivers evaluate subframes too: truly continuous movement, unrelated to step boundaries.
orbit_frames = config['orbit_frames']
rig.driver_add('rotation_euler', 2).driver.expression = f'-0.9 + (frame - 1) * 2 * pi / {orbit_frames}'

def material(name, color, roughness=.5, metallic=0):
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    bsdf = next(n for n in mat.node_tree.nodes if n.type == 'BSDF_PRINCIPLED')
    bsdf.inputs['Base Color'].default_value = (*color, 1)
    bsdf.inputs['Roughness'].default_value = roughness
    bsdf.inputs['Metallic'].default_value = metallic
    return mat

ground_z = lo.z - max(.12, size * .008)
bpy.ops.mesh.primitive_plane_add(size=size * 200, location=(center.x, center.y, ground_z))
floor = bpy.context.object
floor.name = 'Seamless_charcoal_studio'
floor.data.materials.append(material('Charcoal_satin', (.052, .064, .082), .4, .12))

def area(name, position, power, width, color):
    data = bpy.data.lights.new(name, 'AREA')
    data.energy = power * size * size
    data.shape = 'DISK'
    data.size = width * size
    data.color = color
    data.use_shadow = True
    obj = bpy.data.objects.new(name, data)
    scene.collection.objects.link(obj)
    obj.location = center + Vector(position) * size
    obj.rotation_euler = (center - obj.location).to_track_quat('-Z', 'Y').to_euler()
    return obj
area('KEY_large_silk', (1.0, -1.1, 1.5), 95, 1.1, (1.0, .91, .8))
area('FILL_cool_softbox', (-1.2, -.4, .7), 65, 1.4, (.66, .8, 1.0))
area('RIM_strip', (.2, 1.0, 1.1), 120, .8, (.78, .9, 1.0))
area('TOP_silk', (0, 0, 1.8), 60, 1.2, (1.0, .98, .94))
scene.world = bpy.data.worlds.new('Studio_ambient')
scene.world.use_nodes = True
background = next(n for n in scene.world.node_tree.nodes if n.type == 'BACKGROUND')
background.inputs[0].default_value = (.16, .2, .28, 1)
background.inputs[1].default_value = .4
scene.render.engine = 'CYCLES'
scene.cycles.samples = 16
scene.cycles.use_denoising = True
scene.render.engine = 'BLENDER_EEVEE'
scene.eevee.taa_render_samples = config['samples']
scene.render.resolution_x = config['width']
scene.render.resolution_y = config['height']
scene.render.resolution_percentage = 100
scene.render.fps = config['fps']
scene.render.image_settings.file_format = 'PNG'
scene.render.image_settings.color_mode = 'RGB'
scene.render.image_settings.color_depth = '8'
scene.render.image_settings.compression = 10
scene.render.film_transparent = False
scene.view_settings.view_transform = 'AgX'
scene.view_settings.look = 'AgX - Medium High Contrast'
scene.view_settings.exposure = 0
scene.render.use_file_extension = True
scene.render.use_persistent_data = True
scene.frame_start = 1
build_end = timeline['intro_frames'] + len(timeline['steps']) * timeline['step_frames']
scene.frame_end = build_end + timeline['hero_frames'] + timeline['tail_frames']
frame_dir = folder / 'frames'
frame_dir.mkdir(exist_ok=True)
scene.render.filepath = str(frame_dir / 'frame_')

current = set()
for guid, objects in groups.items():
    for obj in objects:
        obj.hide_render = True
        obj.hide_viewport = True
        obj.keyframe_insert('hide_render', frame=1)
        obj.keyframe_insert('hide_viewport', frame=1)
for step in timeline['steps']:
    active = set(step['active'])
    frame = timeline['intro_frames'] + (step['step'] - 1) * timeline['step_frames'] + 1
    for guid in current.symmetric_difference(active):
        for obj in groups[guid]:
            obj.hide_render = guid not in active
            obj.hide_viewport = guid not in active
            obj.keyframe_insert('hide_render', frame=frame)
            obj.keyframe_insert('hide_viewport', frame=frame)
    current = active
    scene.timeline_markers.new(f"{step['step']:03d} {step['op']}", frame=frame)
scene.timeline_markers.new('FINAL / 360 DEGREE HERO ORBIT', frame=build_end + 1)

# Verify every historical state and ensure the machine never receives animation transforms.
for step in timeline['steps']:
    frame = timeline['intro_frames'] + (step['step'] - 1) * timeline['step_frames'] + 1
    scene.frame_set(frame)
    visible = {guid for guid, objs in groups.items() if any(not o.hide_render for o in objs)}
    assert visible == set(step['active']), (step['step'], visible.symmetric_difference(step['active']))
assert all(obj.parent is None for obj in geometry)
scene.frame_set(min(scene.frame_end, build_end + 1))
bpy.ops.file.pack_all()
bpy.ops.wm.save_as_mainfile(filepath=str(folder / 'scene.blend'))
# Project the union of every historical geometry state across one complete orbit.
# This conservative envelope also defines the area excluded by the flicker detector.
from bpy_extras.object_utils import world_to_camera_view
screen_min, screen_max = [1., 1.], [0., 0.]
for sample in range(120):
    frame = 1 + sample * orbit_frames / 120
    scene.frame_set(math.floor(frame), subframe=frame % 1)
    bpy.context.view_layer.update()
    for corner in points:
        projected = world_to_camera_view(scene, camera, corner)
        if not (camera_data.clip_start < projected.z < camera_data.clip_end):
            raise RuntimeError('Camera depth clips historical geometry.')
        for axis in range(2):
            screen_min[axis] = min(screen_min[axis], projected[axis])
            screen_max[axis] = max(screen_max[axis], projected[axis])
if min(screen_min) < .01 or max(screen_max) > .99:
    raise RuntimeError(f'Camera clips model: {screen_min}, {screen_max}')
verification = {
    'historical_steps_verified': len(timeline['steps']), 'imported_variants': len(groups),
    'mesh_objects': len(geometry), 'fixed_center': list(center), 'bounds_min': list(lo),
    'bounds_max': list(hi), 'screen_bounds_min': screen_min, 'screen_bounds_max': screen_max,
    'camera_radius': radius, 'camera_clip': [camera_data.clip_start, camera_data.clip_end],
    'orbit_period_frames': orbit_frames, 'frame_count': scene.frame_end, 'fps': config['fps'],
    'resolution': [config['width'], config['height']], 'engine': scene.render.engine,
    'samples': scene.eevee.taa_render_samples, 'model_global_transform_animated': False,
}
temporary = folder / 'scene_verification.json.tmp'
temporary.write_text(json.dumps(verification, indent=2), encoding='utf-8')
temporary.replace(folder / 'scene_verification.json')
print('SCENE_VERIFIED', json.dumps(verification), flush=True)
