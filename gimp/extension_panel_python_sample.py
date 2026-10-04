#!/usr/bin/env python3
"""Minimal Python plug-in pattern for a host-rendered extension panel.

Install this executable in a GIMP 3 plug-ins subdirectory. It intentionally sends
only text and procedure names across the PDB boundary; the dock widgets stay in GIMP.
"""

import sys

import gi

gi.require_version("Gimp", "3.0")
from gi.repository import Gimp, GLib

ENTRY = "python-fu-extension-panel-sample"
ACTION = ENTRY + "-action"


def _pdb_call(name, values):
    procedure = Gimp.get_pdb().lookup_procedure(name)
    if procedure is None:
        raise RuntimeError(f"PDB procedure not found: {name}")
    config = procedure.create_config()
    for key, value in values.items():
        config.set_property(key, value)
    result = procedure.run(config)
    if result.index(0).get_enum() != Gimp.PDBStatusType.SUCCESS:
        raise RuntimeError(f"PDB call failed: {name}")


def _action(procedure, config, data):
    _pdb_call("gimp-extension-panel-update", {
        "identifier": "python-sample",
        "content": "pg_abc123\tPage 1 — updated from Python",
        "selected-item": "pg_abc123",
    })
    return procedure.new_return_values(Gimp.PDBStatusType.SUCCESS, GLib.Error())


class ExtensionPanelSample(Gimp.PlugIn):
    def do_query_procedures(self):
        return [ENTRY]

    def do_create_procedure(self, name):
        if name != ENTRY:
            return None
        procedure = Gimp.Procedure.new(
            self, name, Gimp.PDBProcType.PERSISTENT, self._run, None, None)
        procedure.set_documentation(
            "Register a Python supplied extension panel",
            "Minimal IPC sample: host renders the dock and Python handles callbacks.",
            name)
        procedure.set_attribution("imanganation", "imanganation", "2026")
        return procedure

    def _run(self, procedure, config, data):
        action = Gimp.Procedure.new(
            self, ACTION, Gimp.PDBProcType.TEMPORARY, _action, None, None)
        action.set_documentation("Update sample panel", "Called by the host action button.", ACTION)
        self.add_temp_procedure(action)

        _pdb_call("gimp-extension-panel-register", {
            "identifier": "python-sample",
            "title": "Python Sample",
            "content": "pg_abc123\tPage 1",
            "presentation": "list",
            "selected-item": "pg_abc123",
            "action-label": "Update",
            "action-procedure": ACTION,
            "item-action-procedure": "",
        })

        procedure.persistent_ready()
        self.persistent_enable()
        GLib.MainLoop().run()
        return procedure.new_return_values(Gimp.PDBStatusType.SUCCESS, GLib.Error())


Gimp.main(ExtensionPanelSample.__gtype__, sys.argv)
