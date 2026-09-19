"""Regenerate the Plasma variant, measurements, and previews from the legacy STLs."""

import json
import hashlib
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from mpl_toolkits.mplot3d.art3d import Poly3DCollection
import numpy as np
import trimesh
import manifold3d
from scipy.interpolate import PchipInterpolator

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "stl/plasma-2350-w"
IMAGES = ROOT / "images/plasma-2350-w"
ENGINE = "manifold"
CENTER = np.array([0.14457, -0.01476])
MOUNT_X = 9.5
PORT_WIDTH = 11.6
PORT_HEIGHT = 6.8
PORT_CENTER_Z = 18
PORT_DEPTH = (57, 92)
LOGO_WALL = 3.2


def logo_tools(body):
    """Smooth back offset of the intact front, interpolating only across openings."""
    xs = np.arange(-40., 40.01, .5)
    zs = np.arange(6., 105.01, .5)
    xx, zz = np.meshgrid(xs, zs)
    origins = np.column_stack((xx.ravel(), np.full(xx.size, -110.), zz.ravel()))
    locations, rays, faces = body.ray.intersects_location(
        origins, np.tile([0., 1., 0.], (len(origins), 1)), multiple_hits=True)
    valid = (body.face_normals[faces, 1] < -.5) & (locations[:, 1] < -45)
    front = np.full(xx.size, np.inf)
    np.minimum.at(front, rays[valid], locations[valid, 1])
    front = front.reshape(xx.shape)
    for col in range(len(xs)):
        present = np.isfinite(front[:, col])
        assert present.sum() > 20, "Insufficient intact front surface"
        front[:, col] = PchipInterpolator(zs[present], front[present, col])(zs)
    # End in open space above the ears, not in the supported crown.
    keep = zs <= 84
    zs, xx, zz, front = zs[keep], xx[keep], zz[keep], front[keep]
    vertices = np.column_stack((xx.ravel(), front.ravel() + LOGO_WALL, zz.ravel()))
    back = vertices.copy()
    back[:, 1] = -30
    size, width = len(vertices), len(xs)
    faces = []
    for row in range(len(zs) - 1):
        for col in range(width - 1):
            a = row * width + col
            faces.extend([[a, a + 1, a + width], [a + 1, a + width + 1, a + width]])
    front_faces = np.asarray(faces)
    faces.extend((front_faces[:, ::-1] + size).tolist())
    perimeter = (list(range(width)) +
                 [r * width + width - 1 for r in range(1, len(zs))] +
                 list(range(size - 2, size - width - 1, -1)) +
                 [r * width for r in range(len(zs) - 2, 0, -1)])
    for a, c in zip(perimeter, perimeter[1:] + perimeter[:1]):
        faces.extend([[a, c, c + size], [a, c + size, a + size]])
    tool = trimesh.Trimesh(vertices=np.vstack((vertices, back)), faces=faces)
    tool.fix_normals()
    # This boundary lies in the carved openings, except the original lower joins.
    outline = [[-39, 6], [40, 6], [40, 44], [28, 57],
               [28, 84], [-26, 84], [-29, 52], [-39, 41]]
    footprint = manifold3d.CrossSection([np.asarray(outline, dtype=float)])
    raw = footprint.extrude(80).to_mesh()
    mask = trimesh.Trimesh(vertices=raw.vert_properties[:, :3], faces=raw.tri_verts)
    mask.vertices = mask.vertices[:, [0, 2, 1]]
    mask.vertices[:, 1] -= 110
    mask.fix_normals()
    return intersection(tool, mask), mask


def box(bounds):
    bounds = np.asarray(bounds, dtype=float)
    return trimesh.creation.box(
        extents=bounds[1] - bounds[0],
        transform=trimesh.transformations.translation_matrix(bounds.mean(axis=0)),
    )


def union(*meshes):
    return trimesh.boolean.union(meshes, engine=ENGINE)


def difference(a, *others):
    return trimesh.boolean.difference([a, *others], engine=ENGINE)


def intersection(a, b):
    return trimesh.boolean.intersection([a, b], engine=ENGINE)


def original(name):
    mesh = trimesh.load_mesh(ROOT / f"stl/pumpkin-{name}.stl")
    # Legacy files are Y-up. All new files are Z-up, with the base on Z=0.
    transform = np.array([[1, 0, 0, 0], [0, 0, -1, 0],
                          [0, 1, 0, 18], [0, 0, 0, 1]])
    mesh.apply_transform(transform)
    return mesh


def prism_xz(points, y0, y1):
    """Extrude a convex X/Z polygon along Y without a triangulation dependency."""
    vertices = [[x, y, z] for y in [y0, y1] for x, z in points]
    count = len(points)
    faces = []
    for i in range(1, count - 1):
        faces.extend([[0, i + 1, i], [count, count + i, count + i + 1]])
    for i in range(count):
        j = (i + 1) % count
        faces.extend([[i, j, count + j], [i, count + j, count + i]])
    mesh = trimesh.Trimesh(vertices=vertices, faces=faces)
    mesh.fix_normals()
    return mesh


def port_tools():
    half = PORT_WIDTH / 2
    lower, upper = PORT_CENTER_Z - PORT_HEIGHT / 2, PORT_CENTER_Z + PORT_HEIGHT / 2
    port = prism_xz([[-half, lower], [half, lower], [half, upper],
                     [-half, upper]], *PORT_DEPTH)
    # Diamond bores have 45-degree roofs, unlike horizontal circular holes.
    screws = []
    for x in [-MOUNT_X, MOUNT_X]:
        r = 2.4
        screws.append(prism_xz([[x-r, 18], [x, 18-r], [x+r, 18], [x, 18+r]], 57, 92))
    return [port, *screws]


def usb_body(body):
    # Refill the old micro-B aperture and bores before making the new pattern.
    # This plate stays in the legacy recessed panel, behind its outer hood.
    plate = box([[-17, 71, 13], [17, 73, 29]])
    return difference(union(body, plate), *port_tools())


def holder(old):
    plate = intersection(old, box([[-100, -100, -1], [100, 100, 5]]))
    pillars, drills, nuts = [], [], []
    # Two diagonal holes, measured from the W pinout vector drawing:
    # 55.6 x 17.6 mm center separation (not the Pi Zero's four-hole pattern).
    for x, y in [(-27.8, -8.8), (27.8, 8.8)]:
        pillar = trimesh.creation.revolve([[0, 0], [4.4, 0], [3.2, 6], [0, 6]],
                                           sections=64)
        pillar.apply_translation([x, y, 5])
        pillars.append(pillar)
        drill = trimesh.creation.cylinder(radius=1.2, height=14, sections=48)
        drill.apply_translation([x, y, 6])
        drills.append(drill)
        nut = trimesh.creation.revolve(
            [[0, -.1], [4.3 / np.sqrt(3), -.1], [4.3 / np.sqrt(3), 2],
             [1.1, 3.5], [0, 3.5]], sections=6)
        nut.apply_translation([x, y, 0])
        nuts.append(nut)
    return difference(union(plate, *pillars), *drills, *nuts), plate


def unsupported(mesh):
    return (mesh.face_normals[:, 2] < -np.sqrt(0.5) - 1e-5) & (mesh.triangles_center[:, 2] > 0.01)


def new_overhangs(mesh, baseline):
    ids = np.flatnonzero(unsupported(mesh))
    if not len(ids):
        return ids
    _, distance, _ = trimesh.proximity.closest_point(baseline, mesh.triangles_center[ids])
    return ids[distance > 0.002]


def usb_bridge_faces(mesh, ids):
    """Allow only the explicitly requested flat USB ceiling, not other overhangs."""
    triangles = mesh.triangles[ids]
    upper = PORT_CENTER_Z + PORT_HEIGHT / 2
    tolerance = .002
    return (
        np.all(abs(triangles[:, :, 2] - upper) < tolerance, axis=1)
        & np.all(abs(triangles[:, :, 0]) <= PORT_WIDTH / 2 + tolerance, axis=1)
        & np.all(triangles[:, :, 1] >= PORT_DEPTH[0] - tolerance, axis=1)
        & np.all(triangles[:, :, 1] <= PORT_DEPTH[1] + tolerance, axis=1)
        & (mesh.face_normals[ids, 2] < -1 + tolerance)
    )


def validate_usb_opening(mesh):
    section = mesh.section([0, 1, 0], [0, 72, 0])
    expected = np.array([[-PORT_WIDTH / 2, PORT_CENTER_Z - PORT_HEIGHT / 2],
                         [PORT_WIDTH / 2, PORT_CENTER_Z + PORT_HEIGHT / 2]])
    for line in section.discrete:
        points = line[:, [0, 2]]
        bounds = np.array([points.min(axis=0), points.max(axis=0)])
        if np.allclose(bounds, expected, atol=.002, rtol=0):
            area = abs(np.sum(points[:-1, 0] * points[1:, 1]
                              - points[1:, 0] * points[:-1, 1])) / 2
            assert abs(area - PORT_WIDTH * PORT_HEIGHT) < .002
            return
    raise AssertionError("USB opening is not the specified rectangle")


def logo_measurements(mesh, baseline):
    points = [(x, z) for x in np.arange(-25.25, 26, 1)
              for z in np.arange(44.25, 81, 1)]
    points += [(x, z) for x in np.arange(-8.25, 9, 1)
               for z in np.arange(18.25, 44, 1)]
    points += [(x, z) for x in np.arange(-32.25, -8, 1)
               for z in np.arange(30.25, 41, 1)]
    origins = np.array([[x, -110, z] for x, z in points])
    directions = np.tile([0., 1., 0.], (len(points), 1))

    def crossings(solid):
        locations, rays, faces = solid.ray.intersects_location(origins, directions)
        values = [[] for _ in points]
        for point, ray, face in zip(locations, rays, faces):
            if point[1] < -45:
                values[ray].append((point[1], solid.face_normals[face, 1]))
        return [sorted(value) for value in values]

    old, new = crossings(baseline), crossings(mesh)
    thickness = []
    front_error = []
    for before, after in zip(old, new):
        assert len(before) == len(after), "Octocat silhouette changed"
        # Rays grazing a carved edge measure the bevel, not front-to-back wall depth.
        if len(before) == 2 and before[0][1] < -.5:
            thickness.append(after[1][0] - after[0][0])
            front_error.append(abs(after[0][0] - before[0][0]))
    assert len(thickness) > 2000, "Insufficient logo wall samples"
    assert max(front_error) < .002, "Visible Octocat surface changed"
    assert max(abs(np.asarray(thickness) - LOGO_WALL)) < .06, "Uneven Octocat wall"
    return {"target_front_to_back_mm": LOGO_WALL, "samples": len(thickness),
            "min_mm": float(min(thickness)), "max_mm": float(max(thickness)),
            "max_front_surface_change_mm": float(max(front_error))}


def mesh_stats(mesh):
    return {"volume_cm3": round(mesh.volume / 1000, 3),
            "bounds_mm": np.round(mesh.bounds, 4).tolist(),
            "watertight": bool(mesh.is_watertight),
            "winding_consistent": bool(mesh.is_winding_consistent),
            "components": len(mesh.split()),
            "triangles": len(mesh.faces)}


def volume(mesh):
    return 0. if mesh.is_empty else abs(mesh.volume)


def changed_volume(a, b):
    if a.is_empty:
        return volume(b)
    if b.is_empty:
        return volume(a)
    return volume(difference(a, b)) + volume(difference(b, a))


def draw_mesh(ax, mesh, color, alpha=1):
    collection = Poly3DCollection(mesh.triangles, facecolors=color, linewidth=0,
                                  alpha=alpha, shade=True,
                                  lightsource=matplotlib.colors.LightSource(azdeg=225, altdeg=45))
    ax.add_collection3d(collection)


def set_view(ax, bounds, elev=24, azim=-65):
    center = np.mean(bounds, axis=0)
    size = max(np.ptp(bounds, axis=0))
    for setter, coordinate in zip([ax.set_xlim, ax.set_ylim, ax.set_zlim], center):
        setter(coordinate - size * .53, coordinate + size * .53)
    ax.set_box_aspect([1, 1, 1])
    ax.view_init(elev=elev, azim=azim)
    ax.set_axis_off()


def previews(old_body, new_body, old_holder, new_holder, logo_mask):
    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 12,
                         "figure.facecolor": "#101923", "text.color": "#edf3f7",
                         "axes.facecolor": "#101923", "axes.titlecolor": "#edf3f7"})
    fig = plt.figure(figsize=(14, 10))
    region = intersection(logo_mask, box([[-42, -100, 18], [42, -40, 84]]))
    for index, (mesh, title) in enumerate([
            (old_body, "Original: thick upper head"),
            (new_body, "Plasma body: smooth, uniform 3.2 mm logo")]):
        ax = fig.add_subplot(2, 2, index + 1, projection="3d")
        draw_mesh(ax, intersection(mesh, region), "#efa63b")
        set_view(ax, [[-40, -90, 18], [40, -50, 84]], elev=12, azim=75)
        ax.set_title(title)
        ax = fig.add_subplot(2, 2, index + 3)
        section = mesh.section([1, 0, 0], [0, 0, 0])
        for line in section.discrete:
            ax.plot(line[:, 1], line[:, 2], color="#ff9f1c", linewidth=2)
        ax.set(xlim=(-89, -57), ylim=(18, 80), aspect="equal")
        ax.set_xlabel("Front-to-back Y (mm)", color="#edf3f7")
        ax.set_ylabel("Height above bed (mm)", color="#edf3f7")
        ax.tick_params(colors="#edf3f7")
        ax.grid(alpha=.2)
    fig.suptitle("Octocat back | same visible silhouette, even thin wall", fontsize=21)
    fig.text(.06, .055, "Rear views of the actual logo; lower plots are centre sections through the head and stem.")
    fig.text(.06, .025, "Original shell retained. Front sculpting preserved; no new >45-degree logo overhangs.")
    fig.savefig(IMAGES / "logo-comparison.png", dpi=120)
    plt.close(fig)

    fig = plt.figure(figsize=(14, 8))
    for i, (mesh, title) in enumerate([(old_holder, "Original: four Pi Zero bosses"),
                                      (new_holder, "Plasma 2350 W: two M2 mounts")]):
        ax = fig.add_subplot(1, 2, i + 1, projection="3d")
        draw_mesh(ax, mesh, "#849eb7" if i == 0 else "#35bdac")
        if i:
            pcb = box([[-30, -11, 11], [30, 11, 12.6]])
            draw_mesh(ax, pcb, "#7eaf67", .35)
        set_view(ax, [[-46, -29, 0], [46, 29, 25]], elev=42, azim=-70)
        ax.set_title(title, pad=0)
    fig.suptitle("Same snap-fit plate | new board mounting pattern", fontsize=22)
    fig.text(.07, .12, "Ghost = 60 x 22 mm PCB envelope, not component-accurate electronics.", color="#ff9f1c")
    fig.text(.07, .075, "Diagonal 55.6 x 17.6 mm hole spacing; 6 mm underside clearance; M2 through-bolts.")
    fig.text(.07, .035, "Base perimeter retained exactly; underside captive-nut pockets are new.")
    fig.savefig(IMAGES / "holder-comparison.png", dpi=120)
    plt.close(fig)

    fig, axes = plt.subplots(1, 2, figsize=(14, 8))
    for ax, mesh, title in zip(axes, [old_body, new_body],
                               ["Original micro-B pattern", "USB-C prototype pattern"]):
        section = mesh.section([0, 1, 0], [0, 72, 0])
        for line in section.discrete:
            ax.plot(line[:, 0], line[:, 2], color="#ff9f1c", linewidth=2)
        ax.set_xlim(-19, 19)
        ax.set_ylim(10, 33)
        ax.set_aspect("equal")
        ax.set_title(title)
        ax.set_xlabel("X (mm)", color="#edf3f7")
        ax.set_ylabel("Height above bed (mm)", color="#edf3f7")
        ax.tick_params(colors="#edf3f7")
        ax.grid(alpha=.2)
    fig.suptitle("Rear panel | rectangular USB-C opening", fontsize=22)
    fig.text(.06, .13, "11.6 x 6.8 mm rectangle; flat bridged ceiling; nominal 19 mm screw spacing.", color="#ff9f1c")
    fig.text(.06, .08, "Cable drawing omits cutout and screw pitch: print the fit coupon before the body.")
    fig.text(.06, .035, "Triangular vent removed. Short bridge accepted for the Bambu X1C; screw bores unchanged.")
    fig.savefig(IMAGES / "usb-comparison.png", dpi=120)
    plt.close(fig)


def main():
    OUT.mkdir(exist_ok=True)
    IMAGES.mkdir(parents=True, exist_ok=True)
    body, old_holder, plug = original("body"), original("pi-holder"), original("plug")
    new_holder, plate = holder(old_holder)
    relief, logo_mask = logo_tools(body)
    full = usb_body(difference(body, relief))
    coupon = difference(box([[-17, 71, 12], [17, 73, 31]]), *port_tools())
    coupon_transform = np.array([[1, 0, 0, 0], [0, 0, 1, -12],
                                 [0, -1, 0, 73], [0, 0, 0, 1]])
    coupon.apply_transform(coupon_transform)
    models = {"plasma2350-holder": new_holder, "pumpkin-body": full,
              "usb-c-fit-coupon": coupon}
    report = {"original_body": mesh_stats(body), "models": {},
              "source_sha256": {
                  path.name: hashlib.sha256(path.read_bytes()).hexdigest()
                  for path in sorted((ROOT / "stl").glob("*.stl"))},
              "parameters": {"octocat_front_to_back_mm": LOGO_WALL,
                             "pcb_mm": [60, 22], "mount_pitch_mm": [55.6, 17.6],
                             "usb_clear_rectangle_mm": [PORT_WIDTH, PORT_HEIGHT],
                             "usb_roof": "flat bridge",
                             "usb_bridge_span_mm": PORT_WIDTH,
                             "usb_screw_pitch_mm_provisional": 2 * MOUNT_X}}
    for name, mesh in models.items():
        assert mesh.is_volume and len(mesh.split()) == 1, f"Invalid solid: {name}"
        path = OUT / f"{name}.stl"
        mesh.export(path)
        exported = trimesh.load_mesh(path)
        assert exported.is_volume and len(exported.split()) == 1
        report["models"][name] = mesh_stats(exported)
        models[name] = exported
    new_holder = models["plasma2350-holder"]
    full = models["pumpkin-body"]
    coupon_in_mount = models["usb-c-fit-coupon"].copy()
    coupon_in_mount.apply_transform(np.linalg.inv(coupon_transform))
    for mesh in (full, coupon_in_mount):
        validate_usb_opening(mesh)
    report["body_octocat_wall"] = logo_measurements(full, body)
    for name, mesh in [("body", full), ("holder", new_holder)]:
        ids = new_overhangs(mesh, old_holder if name == "holder" else body)
        allowed = usb_bridge_faces(mesh, ids) if name != "holder" else np.zeros(len(ids), dtype=bool)
        bridge_ids, unexpected_ids = ids[allowed], ids[~allowed]
        unexpected_area = float(mesh.area_faces[unexpected_ids].sum())
        report[f"{name}_new_overhangs_over_45"] = {
            "triangles": len(ids), "area_mm2": float(mesh.area_faces[ids].sum()),
            "allowed_usb_bridge_triangles": len(bridge_ids),
            "allowed_usb_bridge_area_mm2": float(mesh.area_faces[bridge_ids].sum()),
            "unexpected_triangles": len(unexpected_ids),
            "unexpected_area_mm2": unexpected_area,
        }
        assert unexpected_area < .01, f"New unsupported {name} surface: {unexpected_area:.3f} mm2"
        if name != "holder":
            assert len(bridge_ids), "Missing flat USB bridge"
            span = np.ptp(mesh.triangles[bridge_ids, :, 0])
            assert abs(span - PORT_WIDTH) < .002, "Unexpected USB bridge span"
    base_region = box([[-100, -100, -1], [100, 100, 6]])
    crown_region = box([[-100, -100, 112], [100, 100, 150]])
    for label, region in [("base", base_region), ("stalk_seat", crown_region)]:
        before, after = intersection(body, region), intersection(full, region)
        changed = changed_volume(before, after)
        assert changed < .01, f"Changed {label}: {changed}"
        report[f"{label}_symmetric_difference_mm3"] = changed
    rim = difference(box([[-50, -30, -1], [50, 30, 5]]),
                     box([[-34, -15, -2], [34, 15, 6]]))
    rim_change = changed_volume(intersection(plate, rim), intersection(new_holder, rim))
    assert rim_change < .01, "Holder snap rim changed"
    report["holder_snap_rim_symmetric_difference_mm3"] = rim_change
    collision = intersection(full, new_holder)
    collision_volume = volume(collision)
    assert collision_volume < .01, "Holder collides with body"
    report["holder_body_intersection_mm3"] = collision_volume
    # Both old and new holders must still work with either body.
    assert volume(intersection(body, new_holder)) < .01
    assert volume(intersection(full, old_holder)) < .01
    plug_before = intersection(body, plug)
    plug_after = intersection(full, plug)
    assert changed_volume(plug_before, plug_after) < .01, "Stalk fit changed"
    centers = body.triangles_center
    radial = centers[:, :2] - CENTER
    facing = np.sum(body.face_normals[:, :2] * radial, axis=1) / np.linalg.norm(radial, axis=1)
    exterior = (facing > .6) & (centers[:, 2] > 7) & (centers[:, 2] < 100) & (abs(centers[:, 0]) > 23)
    _, distance, _ = trimesh.proximity.closest_point(full, centers[exterior])
    assert distance.max() < .002, "An intact outer lobe was cut through"
    report["outer_lobe_samples_retained"] = int(exterior.sum())
    report["outer_lobe_max_distance_mm"] = float(distance.max())
    report["original_overhang_area_over_45_mm2"] = float(body.area_faces[unsupported(body)].sum())
    (OUT / "measurements.json").write_text(json.dumps(report, indent=2) + "\n")
    previews(body, full, old_holder, new_holder, logo_mask)
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
