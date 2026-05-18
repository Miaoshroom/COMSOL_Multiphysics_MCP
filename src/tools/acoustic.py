"""Acoustic absorption workflow tools for a reusable COMSOL template."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
import shutil
import re
from typing import Optional, Sequence

from mcp.server.fastmcp import FastMCP

from .session import session_manager


DEFAULT_TEMPLATE = "/Users/neko/Developer/Projects/comsol/test.mph"


def _model(model_name: Optional[str] = None):
    model = session_manager.get_model(model_name)
    if model is None:
        raise ValueError(f"Model not found: {model_name or 'no current model'}")
    return model


def _entities(node) -> list[int]:
    try:
        return list(node.selection().entities())
    except Exception:
        return []


def _get_property(node, name: str, default=None):
    try:
        return node.get(name)
    except Exception:
        try:
            return node.getString(name)
        except Exception:
            return default


def _set_selection(node, entities: Optional[Sequence[int]]) -> None:
    if entities is not None:
        node.selection().set(list(entities))


def _try_set_selection(node, entities: Optional[Sequence[int]]) -> dict:
    if entities is None:
        return {"changed": False, "selection": _entities(node)}
    try:
        node.selection().set(list(entities))
        return {"changed": True, "selection": _entities(node)}
    except Exception as exc:
        return {"changed": False, "selection": _entities(node), "error": str(exc)}


def _set_block(feature, pos: Sequence[str], size: Sequence[str]) -> None:
    feature.set("pos", list(pos))
    feature.set("size", list(size))


def _str_list(values: Sequence[float | int | str]) -> list[str]:
    return [str(value) for value in values]


def _mm_value(value: float | int | str) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    match = re.search(r"[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?", value)
    if not match:
        raise ValueError(f"Cannot parse millimeter value: {value!r}")
    return float(match.group(0))


def _mm_expr(value: float) -> str:
    return f"{value:g}[mm]"


def _unique_sorted(values: Sequence[int]) -> list[int]:
    return sorted({int(value) for value in values})


def _box_entities(
    comp,
    tag: str,
    dim: int,
    bounds: Sequence[float],
    condition: str = "intersects",
) -> list[int]:
    try:
        comp.selection().remove(tag)
    except Exception:
        pass
    sel = comp.selection().create(tag, "Box")
    sel.geom("geom1", dim)
    sel.set("entitydim", str(dim))
    for key, value in zip(("xmin", "xmax", "ymin", "ymax", "zmin", "zmax"), bounds):
        sel.set(key, _mm_expr(value))
    sel.set("condition", condition)
    return _unique_sorted(sel.entities())


def _domain_at_point(comp, tag: str, point: Sequence[float], eps: float = 0.05) -> list[int]:
    x, y, z = point
    return _box_entities(
        comp,
        tag,
        3,
        (x - eps, x + eps, y - eps, y + eps, z - eps, z + eps),
        "intersects",
    )


def _plane_boundaries(
    comp,
    tag: str,
    xmin: float,
    xmax: float,
    ymin: float,
    ymax: float,
    z: float,
    eps: float = 0.001,
) -> list[int]:
    return _box_entities(
        comp,
        tag,
        2,
        (xmin, xmax, ymin, ymax, z - eps, z + eps),
        "inside",
    )


def register_acoustic_tools(mcp: FastMCP) -> None:
    """Register acoustic absorption template workflow tools."""

    @mcp.tool()
    def acoustic_prepare_working_model(
        template_path: str = DEFAULT_TEMPLATE,
        output_dir: Optional[str] = None,
        run_name: Optional[str] = None,
        load: bool = True,
        overwrite: bool = False,
    ) -> dict:
        """
        Copy the treasured acoustic template to a working .mph file, then optionally load it.

        Args:
            template_path: Source .mph template; never modified by this tool
            output_dir: Directory for the working copy (default: template directory)
            run_name: Base name for the copied model (default: timestamped)
            load: Whether to load the copied model into the current COMSOL session
            overwrite: Whether to overwrite an existing output file

        Returns:
            Copied path and loaded model information
        """
        if not session_manager.is_connected:
            return {"success": False, "error": "No active COMSOL session. Call comsol_start_server first."}

        source = Path(template_path).expanduser()
        if not source.exists():
            return {"success": False, "error": f"Template file not found: {source}"}
        if source.suffix.lower() != ".mph":
            return {"success": False, "error": f"Template must be a .mph file: {source}"}

        target_dir = Path(output_dir).expanduser() if output_dir else source.parent
        target_dir.mkdir(parents=True, exist_ok=True)

        safe_name = run_name or f"{source.stem}_work_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        if not safe_name.endswith(".mph"):
            safe_name = f"{safe_name}.mph"
        target = target_dir / safe_name

        if target.exists() and not overwrite:
            return {"success": False, "error": f"Output already exists: {target}"}

        shutil.copy2(source, target)

        result = {
            "success": True,
            "template": str(source),
            "working_file": str(target),
            "loaded": False,
        }

        if load:
            client = session_manager.client
            if client is None:
                return {"success": False, "error": "COMSOL client not available after copy.", "working_file": str(target)}
            model = client.load(str(target))
            model_name = session_manager.add_model(model)
            session_manager.set_current_model(model_name)
            result["loaded"] = True
            result["model"] = model_name

        return result

    @mcp.tool()
    def acoustic_template_summary(model_name: Optional[str] = None) -> dict:
        """
        Summarize key tags and selections in the acoustic absorption template.

        Args:
            model_name: Model name (default: current model)

        Returns:
            Node tags, labels, and key domain/boundary selections
        """
        try:
            model = _model(model_name)
            comp = model.java.component("comp1")
            geom = comp.geom("geom1")
            mesh = comp.mesh("mesh1")
            acpr = comp.physics("acpr")

            summary = {
                "model": model.name(),
                "geometry": {
                    "block_air": "blk1",
                    "difference": "dif1",
                    "upper_layer_block": "blk2",
                    "upper_layer_copy_move": "mov8",
                    "features": list(geom.feature().tags()),
                },
                "definitions": {
                    "integration": {"tag": "intop3", "operator": _get_property(comp.cpl("intop3"), "opname"), "selection": _entities(comp.cpl("intop3"))},
                    "average": {"tag": "aveop2", "operator": _get_property(comp.cpl("aveop2"), "opname"), "selection": _entities(comp.cpl("aveop2"))},
                    "pml": {"tag": "pml1", "selection": _entities(comp.coordSystem("pml1"))},
                },
                "physics": {
                    "pressure_acoustics": {"tag": "acpr", "selection": _entities(acpr.feature("fpam1"))},
                    "thermoviscous_boundary_layer": {"tag": "tvb1", "selection": _entities(acpr.feature("tvb1"))},
                    "background_pressure_field": {"tag": "bpf1", "selection": _entities(acpr.feature("bpf1"))},
                },
                "mesh": {
                    "size": "size",
                    "free_tet": {"tag": "ftet1", "selection": _entities(mesh.feature("ftet1"))},
                    "sweep": {"tag": "swe1", "selection": _entities(mesh.feature("swe1"))},
                },
                "results": {
                    "absorption_plot": "pg7",
                    "impedance_plot": "pg8",
                    "data_export": "data1",
                },
            }
            return {"success": True, "summary": summary}
        except Exception as e:
            return {"success": False, "error": str(e)}

    @mcp.tool()
    def acoustic_configure_geometry_layers(
        model_name: Optional[str] = None,
        air_pos: Sequence[str] = ("-75[mm]", "-25[mm]", "0[mm]"),
        air_size: Sequence[str] = ("100[mm]", "100[mm]", "50[mm]"),
        layer_height: str = "20[mm]",
        build: bool = True,
    ) -> dict:
        """
        Configure the air-domain bounding block and the two stacked upper blocks.

        This updates the existing template geometry nodes: blk1, blk2, mov8.
        The difference node dif1 is preserved.

        Args:
            model_name: Model name (default: current model)
            air_pos: Lower air bounding block corner position [x, y, z]
            air_size: Lower air bounding block size [x, y, z]
            layer_height: Height of each upper layer block
            build: Whether to rebuild geom1 after editing

        Returns:
            Geometry configuration summary
        """
        try:
            if len(air_pos) != 3 or len(air_size) != 3:
                return {"success": False, "error": "air_pos and air_size must each contain three values."}

            model = _model(model_name)
            geom = model.java.component("comp1").geom("geom1")

            lower_pos = _str_list(air_pos)
            lower_size = _str_list(air_size)

            upper_pos = [lower_pos[0], lower_pos[1], f"({lower_pos[2]})+({lower_size[2]})"]
            upper_size = [lower_size[0], lower_size[1], str(layer_height)]

            _set_block(geom.feature("blk1"), lower_pos, lower_size)
            _set_block(geom.feature("blk2"), upper_pos, upper_size)
            geom.feature("mov8").set("displz", str(layer_height))

            if build:
                geom.run()

            return {
                "success": True,
                "model": model.name(),
                "air_block": {"tag": "blk1", "pos": lower_pos, "size": lower_size},
                "upper_layer_block": {"tag": "blk2", "pos": upper_pos, "size": upper_size},
                "upper_layer_copy": {"tag": "mov8", "displz": str(layer_height)},
                "built": build,
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    @mcp.tool()
    def acoustic_configure_selections(
        model_name: Optional[str] = None,
        integration_boundaries: Optional[Sequence[int]] = None,
        average_boundaries: Optional[Sequence[int]] = None,
        pml_domains: Optional[Sequence[int]] = None,
        pressure_domains: Optional[Sequence[int]] = None,
        background_domains: Optional[Sequence[int]] = None,
        thermoviscous_boundaries: Optional[Sequence[int]] = None,
        free_tet_domains: Optional[Sequence[int]] = None,
        sweep_domains: Optional[Sequence[int]] = None,
        build_mesh: bool = False,
    ) -> dict:
        """
        Configure definitions, PML, acoustic physics, and mesh selections.

        Any selection argument left as None is not changed.

        Args:
            model_name: Model name (default: current model)
            integration_boundaries: Boundary IDs for intop3
            average_boundaries: Boundary IDs for aveop2
            pml_domains: Domain IDs for pml1, typically upper block
            pressure_domains: Domain IDs for the main Pressure Acoustics feature
            background_domains: Domain IDs for Background Pressure Field, typically lower block
            thermoviscous_boundaries: Boundary IDs for Thermoviscous Boundary Layer Impedance
            free_tet_domains: Domain IDs for Free Tetrahedral mesh
            sweep_domains: Domain IDs for Sweep mesh
            build_mesh: Whether to run mesh1 after setting mesh selections

        Returns:
            Updated selections
        """
        try:
            model = _model(model_name)
            comp = model.java.component("comp1")
            acpr = comp.physics("acpr")
            mesh = comp.mesh("mesh1")

            selection_results = {
                "integration_boundaries": _try_set_selection(comp.cpl("intop3"), integration_boundaries),
                "average_boundaries": _try_set_selection(comp.cpl("aveop2"), average_boundaries),
                "pml_domains": _try_set_selection(comp.coordSystem("pml1"), pml_domains),
                "pressure_domains": _try_set_selection(acpr.feature("fpam1"), pressure_domains),
                "background_domains": _try_set_selection(acpr.feature("bpf1"), background_domains),
                "thermoviscous_boundaries": _try_set_selection(acpr.feature("tvb1"), thermoviscous_boundaries),
                "free_tet_domains": _try_set_selection(mesh.feature("ftet1"), free_tet_domains),
                "sweep_domains": _try_set_selection(mesh.feature("swe1"), sweep_domains),
            }

            if build_mesh:
                mesh.run()

            return {
                "success": True,
                "model": model.name(),
                "selections": selection_results,
                "mesh_built": build_mesh,
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    @mcp.tool()
    def acoustic_auto_configure_selections(
        model_name: Optional[str] = None,
        air_pos_mm: Sequence[float] = (0.0, 0.0, 0.0),
        air_size_mm: Sequence[float] = (50.0, 50.0, 50.0),
        layer_height_mm: float = 12.5,
        build_mesh: bool = False,
    ) -> dict:
        """
        Infer and configure the standard acoustic selections from the layer geometry.

        The rule matches the corrected workflow:
        - intop_in selects only the bottom face of the first added upper block.
        - aveop1 selects the interface between the two added upper blocks.
        - pml1 selects the top added block.
        - Background Pressure Field selects the lower of the two added upper blocks.
        - Thermoviscous Boundary Layer Impedance starts from all boundaries, then
          removes the boundaries belonging to the two added upper blocks.

        Args:
            model_name: Model name (default: current model)
            air_pos_mm: Lower air wrapper corner [x, y, z] in mm
            air_size_mm: Lower air wrapper size [lx, ly, lz] in mm
            layer_height_mm: Height of each added upper block in mm
            build_mesh: Whether to run mesh1 after setting selections

        Returns:
            Inferred selections and applied COMSOL selection results
        """
        try:
            if len(air_pos_mm) != 3 or len(air_size_mm) != 3:
                return {"success": False, "error": "air_pos_mm and air_size_mm must each contain three values."}

            model = _model(model_name)
            comp = model.java.component("comp1")
            acpr = comp.physics("acpr")
            mesh = comp.mesh("mesh1")
            geom = comp.geom("geom1")

            x0, y0, z0 = [_mm_value(value) for value in air_pos_mm]
            lx, ly, lz = [_mm_value(value) for value in air_size_mm]
            h = _mm_value(layer_height_mm)
            x1, y1 = x0 + lx, y0 + ly
            z_air_top = z0 + lz
            z_avg = z_air_top + h
            z_pml_mid = z_avg + h / 2
            x_mid, y_mid = x0 + lx / 2, y0 + ly / 2

            integration = _plane_boundaries(comp, "auto_intop_in", x0 - 0.1, x1 + 0.1, y0 - 0.1, y1 + 0.1, z_air_top)
            average = _plane_boundaries(comp, "auto_aveop1", x0 - 0.1, x1 + 0.1, y0 - 0.1, y1 + 0.1, z_avg)

            lower_domains = _domain_at_point(comp, "auto_lower_domain", (x_mid, y_mid, z0 + lz / 2))
            source_domains = _domain_at_point(comp, "auto_background_domain", (x_mid, y_mid, z_air_top + h / 2))
            pml_domains = _domain_at_point(comp, "auto_pml_domain", (x_mid, y_mid, z_pml_mid))

            warnings = []
            feature_tags = set(geom.feature().tags())
            all_boundaries = _box_entities(
                comp,
                "auto_all_boundaries",
                2,
                (x0 - 0.1, x1 + 0.1, y0 - 0.1, y1 + 0.1, z0 - 0.1, z_air_top + 2 * h + 0.1),
                "intersects",
            )
            upper_stack_boundaries = _box_entities(
                comp,
                "auto_upper_stack_boundaries",
                2,
                (x0 - 0.1, x1 + 0.1, y0 - 0.1, y1 + 0.1, z_air_top, z_air_top + 2 * h),
                "inside",
            )
            lower_boundary_candidates = _box_entities(
                comp,
                "auto_lower_internal_boundaries",
                2,
                (x0 + 0.01, x1 - 0.01, y0 + 0.01, y1 - 0.01, z0 + 0.01, z_air_top - 0.001),
                "intersects",
            )

            thermoviscous = [value for value in all_boundaries if value not in set(upper_stack_boundaries)]

            if {"obj_outer", "obj_inner", "obj_hole", "solid_shell", "blk2", "blk3"}.issubset(feature_tags):
                # In this validated shell-with-hole workflow, faces 18 and 19 are
                # opening/contact remnants near the top hole. The GUI marks them
                # as not applicable for this wall condition, so remove them after
                # applying the "select all, then remove upper stack" rule.
                not_applicable = {18, 19}
                thermoviscous = [value for value in thermoviscous if value not in not_applicable]
                if thermoviscous != [10, 11, 12, 13, 14, 15, 16, 20]:
                    warnings.append(
                        "Detected shell-with-hole geometry, but boundary numbering differed from the validated 50 mm test."
                    )
            else:
                warnings.append(
                    "Thermoviscous boundaries were inferred from the lower internal air box; verify them in COMSOL for new geometry families."
                )

            pressure_domains = _unique_sorted(lower_domains + source_domains + pml_domains)
            free_tet_domains = _unique_sorted(lower_domains + source_domains)

            selection_results = {
                "integration_boundaries": _try_set_selection(comp.cpl("intop3"), integration),
                "average_boundaries": _try_set_selection(comp.cpl("aveop2"), average),
                "pml_domains": _try_set_selection(comp.coordSystem("pml1"), pml_domains),
                "pressure_domains": _try_set_selection(acpr.feature("fpam1"), pressure_domains),
                "background_domains": _try_set_selection(acpr.feature("bpf1"), source_domains),
                "thermoviscous_boundaries": _try_set_selection(acpr.feature("tvb1"), thermoviscous),
                "free_tet_domains": _try_set_selection(mesh.feature("ftet1"), free_tet_domains),
                "sweep_domains": _try_set_selection(mesh.feature("swe1"), pml_domains),
            }

            if build_mesh:
                mesh.run()

            return {
                "success": True,
                "model": model.name(),
                "inferred": {
                    "integration_boundaries": integration,
                    "average_boundaries": average,
                    "pml_domains": pml_domains,
                    "pressure_domains": pressure_domains,
                    "background_domains": source_domains,
                    "thermoviscous_boundaries": thermoviscous,
                    "free_tet_domains": free_tet_domains,
                    "sweep_domains": pml_domains,
                    "lower_domain_candidates": lower_domains,
                    "all_boundaries": all_boundaries,
                    "upper_stack_boundaries_removed": upper_stack_boundaries,
                    "lower_internal_boundary_candidates": lower_boundary_candidates,
                },
                "selections": selection_results,
                "warnings": warnings,
                "mesh_built": build_mesh,
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    @mcp.tool()
    def acoustic_set_frequency_sweep(
        model_name: Optional[str] = None,
        frequencies: Optional[str] = None,
        fmax: Optional[str] = None,
    ) -> dict:
        """
        Set the frequency-domain study frequency list and optional fmax parameter.

        Args:
            model_name: Model name (default: current model)
            frequencies: COMSOL frequency list expression for std1/freq, e.g. range(100,10,5000)
            fmax: Optional fmax parameter value, e.g. 5000[Hz]

        Returns:
            Updated study settings
        """
        try:
            model = _model(model_name)
            if fmax is not None:
                model.parameter("fmax", fmax)

            freq = model.java.study("std1").feature("freq")
            if frequencies is not None:
                freq.set("plist", frequencies)

            return {
                "success": True,
                "model": model.name(),
                "frequencies": freq.getString("plist") if hasattr(freq, "getString") else frequencies,
                "fmax": fmax,
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    @mcp.tool()
    def acoustic_build_mesh_solve_export(
        model_name: Optional[str] = None,
        output_dir: Optional[str] = None,
        export_data: bool = True,
        export_plot: bool = True,
        save_model: bool = True,
    ) -> dict:
        """
        Build mesh, solve std1, export absorption-curve results, and optionally save the model.

        Args:
            model_name: Model name (default: current model)
            output_dir: Output directory (default: loaded model directory)
            export_data: Whether to run data1 export
            export_plot: Whether to export the template absorption curve as text and PNG
            save_model: Whether to save the working model

        Returns:
            Output file paths and solve status
        """
        try:
            model = _model(model_name)
            jm = model.java
            base_dir = Path(output_dir).expanduser() if output_dir else Path(model.file()).parent
            base_dir.mkdir(parents=True, exist_ok=True)

            stem = Path(model.file()).stem if model.file() else model.name()
            data_path = base_dir / f"{stem}_data1.txt"
            curve_path = base_dir / f"{stem}_absorption_curve.txt"
            image_path = base_dir / f"{stem}_absorption_curve.png"

            jm.component("comp1").mesh("mesh1").run()
            jm.study("std1").run()

            exported = {}
            if export_data:
                model.export("数据 1", str(data_path))
                exported["data"] = str(data_path)

            if export_plot:
                model.export("绘图 4", str(curve_path))
                exported["absorption_curve"] = str(curve_path)

                image_tag = "img_absorption_curve"
                exports = jm.result().export()
                try:
                    exports.remove(image_tag)
                except Exception:
                    pass
                image = exports.create(image_tag, "Image")
                image.set("sourceobject", "pg7")
                image.set("pngfilename", str(image_path))
                image.set("width", "1000")
                image.set("height", "700")
                image.run()
                exported["absorption_curve_png"] = str(image_path)

            if save_model:
                model.save()

            return {
                "success": True,
                "model": model.name(),
                "mesh_built": True,
                "solved": True,
                "exported": exported,
                "saved": save_model,
            }
        except Exception as e:
            return {"success": False, "error": str(e)}
