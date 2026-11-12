"""Integration checks for Sublime Text, using disposable files only.

Temporarily copy this file to the package root as fix_verification.py. Invoke:
    subl --command 'gpg_fix_verification {"root": "/absolute/temp/directory"}'
    subl --command 'gpg_fix_async_verification {"root": "/absolute/temp/directory"}'
Inspect report.json and async-report.json, then remove the temporary plugin.
Run in this package's Python host, not Packages/User. No personal key is used.
"""
import sublime
import sublime_plugin
import importlib
import imp
import json
import os
import traceback

class GpgFixVerificationCommand(sublime_plugin.WindowCommand):
    def run(self, root):
        try:
            self.verify(root)
        except Exception:
            with open(os.path.join(root, 'setup-error.txt'), 'w') as f:
                f.write(traceback.format_exc())

    def verify(self, root):
        rootmod = importlib.import_module('SublimeGPG.gpg')
        sublime_plugin.unload_module(rootmod)
        imp.reload(importlib.import_module('SublimeGPG.src.text_commands'))
        mod = imp.reload(importlib.import_module('SublimeGPG.src.auto_gpg_listener'))
        imp.reload(importlib.import_module('SublimeGPG.src'))
        rootmod = imp.reload(rootmod)
        loaded = rootmod.plugin_loaded
        rootmod.plugin_loaded = lambda: None
        try:
            sublime_plugin.load_module(rootmod)
        finally:
            rootmod.plugin_loaded = loaded
        original_gpg = mod.gpg
        results = []
        views = []
        def fixture(name, text='edited plaintext', original='original ciphertext'):
            path = os.path.join(root, name)
            with open(path, 'w') as f:
                f.write(original)
            v = self.window.new_file()
            v.retarget(path)
            v.run_command('append', {'characters': text})
            views.append(v)
            return v, path
        def read(path):
            with open(path) as f:
                return f.read()
        def check(name, condition):
            results.append({'name': name, 'passed': bool(condition)})
        try:
            v, p = fixture('failure.asc')
            mod.gpg = lambda *args: None
            self.window.focus_view(v)
            v.run_command('gpg_safe_save')
            check('encryption failure preserves original bytes', read(p) == 'original ciphertext')
            check('encryption failure preserves edits and dirty state', v.substr(sublime.Region(0,v.size())) == 'edited plaintext' and v.is_dirty() and not v.is_scratch())
            v, p = fixture('exception.asc')
            def fail(*args):
                raise OSError('simulated gpg launch failure')
            mod.gpg = fail
            self.window.focus_view(v)
            v.run_command('gpg_safe_save')
            check('gpg exception does not write', read(p) == 'original ciphertext')
            v, p = fixture('empty.asc')
            mod.gpg = lambda *args: ''
            self.window.focus_view(v)
            v.run_command('gpg_safe_save')
            check('empty output does not write', read(p) == 'original ciphertext')
            v, p = fixture('success.asc')
            ciphertext = '-----BEGIN PGP MESSAGE-----\nTEST ONLY\n-----END PGP MESSAGE-----'
            mod.gpg = lambda *args: ciphertext
            self.window.focus_view(v)
            v.run_command('gpg_safe_save')
            check('success writes ciphertext', read(p).strip() == ciphertext)
            check('success restores plaintext in editor', v.substr(sublime.Region(0,v.size())) == 'edited plaintext')
            check('success preserves atomic save setting', v.settings().get('atomic_save', True) is True)
            v, p = fixture('conversion.asc')
            source = os.path.join(root, 'original.txt')
            with open(source, 'w') as f:
                f.write('source plaintext')
            v.settings().set('gpg_delete_original_file', source)
            mod.gpg = lambda *args: None
            self.window.focus_view(v)
            v.run_command('gpg_safe_save')
            check('failed conversion retains original source and target', os.path.exists(source) and read(p) == 'original ciphertext')
            v, p = fixture('save-as.asc')
            target = os.path.join(root, 'new-target.asc')
            v.run_command('gpg_safe_save', {'target': target})
            check('failed Save As neither creates target nor retargets view', not os.path.exists(target) and v.file_name() == p)
            v, p = fixture('save-all.asc')
            self.window.run_command('save_all')
            check('Save All failure preserves original', read(p) == 'original ciphertext')
            v, p = fixture('native-failure.asc')
            mod.gpg = lambda *args: None
            v.run_command('save')
            check('native save failure preserves original ciphertext', read(p) == 'original ciphertext')
            check('native save failure restores dirty plaintext', v.substr(sublime.Region(0,v.size())) == 'edited plaintext' and v.is_dirty() and not v.is_scratch())
            v, p = fixture('native-success.asc')
            mod.gpg = lambda *args: ciphertext
            v.run_command('save')
            check('native save success writes only ciphertext', read(p).strip() == ciphertext)
            check('native save success restores editor plaintext', v.substr(sublime.Region(0,v.size())) == 'edited plaintext')
            v, p = fixture('native-conversion.asc')
            v.settings().set('gpg_delete_original_file', source)
            mod.gpg = lambda *args: None
            v.run_command('save')
            check('native failed conversion never deletes source', os.path.exists(source) and read(p) == 'original ciphertext')
            v, p = fixture('native-binary.gpg')
            original_binary = bytes([255, 0, 13, 10, 128, 1])
            with open(p, 'wb') as f:
                f.write(original_binary)
            mod.gpg = lambda *args: None
            before = os.stat(p).st_mtime_ns if hasattr(os.stat(p), 'st_mtime_ns') else os.stat(p).st_mtime
            v.run_command('save')
            with open(p,'rb') as f:
                binary = f.read()
            after = os.stat(p).st_mtime_ns if hasattr(os.stat(p), 'st_mtime_ns') else os.stat(p).st_mtime
            check('native binary failure preserves exact bytes and mtime', binary == original_binary and before == after)
            check('native binary failure restores filename and atomic save', v.file_name() == p and v.settings().get('atomic_save',True) is True)
            v, p = fixture('native-new.asc')
            os.remove(p)
            v.run_command('save')
            check('native failure does not create a new target', not os.path.exists(p))
        except Exception:
            results.append({'error': traceback.format_exc()})
        finally:
            mod.gpg = original_gpg
            for v in views:
                v.set_scratch(True)
                v.close()
            with open(os.path.join(root,'report.json'),'w') as f:
                json.dump(results,f,ensure_ascii=False,indent=2)

class GpgFixAsyncVerificationCommand(sublime_plugin.WindowCommand):
    def run(self, root):
        mod = importlib.import_module('SublimeGPG.src.auto_gpg_listener')
        original_gpg = mod.gpg
        p = os.path.join(root,'async-native.asc')
        with open(p,'w') as f:
            f.write('original ciphertext')
        before = os.stat(p).st_mtime
        v = self.window.new_file()
        v.retarget(p)
        v.run_command('append', {'characters':'unsaved async edits'})
        mod.gpg = lambda view, *args: None if view.id() == v.id() else original_gpg(view,*args)
        v.run_command('save', {'async': True})
        def check():
            try:
                with open(p) as f:
                    contents = f.read()
                report = {
                    'async native failure preserves original bytes and mtime': contents == 'original ciphertext' and os.stat(p).st_mtime == before,
                    'async native failure restores filename and dirty edits': v.file_name() == p and v.is_dirty() and v.substr(sublime.Region(0,v.size())) == 'unsaved async edits',
                    'async native failure restores atomic save': v.settings().get('atomic_save',True) is True,
                }
                with open(os.path.join(root,'async-report.json'),'w') as f:
                    json.dump(report,f,indent=2)
            finally:
                mod.gpg = original_gpg
                v.set_scratch(True)
                v.close()
        sublime.set_timeout(check, 300)
