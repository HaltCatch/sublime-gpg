#!/usr/bin/env python
# coding: utf-8


import sublime
import sublime_plugin

import os
import subprocess
import threading


# TODO: remove this and its call in plugin_loaded() after a few updates
def temporary_settings_fix():
    s = sublime.load_settings('gpg.sublime-settings')
    if s.has('gpg_command'):
        s.set('gpg.command', s.get('gpg_command', 'gpg'))
        s.erase('gpg_command')
    if s.has('homedir'):
        s.set('gpg.homedir', s.get('homedir', ''))
        s.erase('homedir')
    if s.has('verbosity'):
        s.set('gpg.verbosity', s.get('verbosity', 1))
        s.erase('verbosity')
    if s.has('recipients'):
        s.set('gpg.recipients', s.get('recipients', ''))
        s.erase('recipients')
    if s.has('first_run'):
        s.set('gpg.readme_shown', not s.get('first_run', True))
        s.erase('first_run')
    sublime.save_settings('gpg.sublime-settings')


def plugin_loaded():
    '''
        Sublime 3 calls this once the plugin API is ready.
    '''

    # TODO: remove this fix and its def above after a few updates
    temporary_settings_fix()

    s = sublime.load_settings('gpg.sublime-settings')

    gpg_readme_shown = s.get('gpg.readme_shown', False)
    if not gpg_readme_shown:
        s.set('gpg.readme_shown', True)
        sublime.save_settings('gpg.sublime-settings')
        sublime.set_timeout_async(lambda: sublime.active_window().run_command('gpg_readme'), 5000)

    # 自动解密冷启动恢复的会话
    def check_view(view):
        if view.is_loading():
            sublime.set_timeout_async(lambda: check_view(view), 50)
            return
        filename = view.file_name()
        if filename and (filename.endswith('.asc') or filename.endswith('.gpg')):
            text = view.substr(sublime.Region(0, min(100, view.size())))
            if "BEGIN PGP MESSAGE" in text or "BEGIN PGP SIGNED MESSAGE" in text:
                view.run_command('gpg_auto_decrypt')

    for window in sublime.windows():
        for view in window.views():
            check_view(view)



def gpg(view, data, opts_in):
    '''
        gpg calls the gpg binary to process the data and returns the result.
    '''

    window = view.window()
    s = sublime.load_settings('gpg.sublime-settings')
    gpg_command = s.get('gpg.command', 'gpg')
    opts = [gpg_command,
            '--armor',
            '--batch',
            '--no-greeting',
            '--trust-model', 'always',
            '--yes']
    gpg_homedir = s.get('gpg.homedir', '')
    if gpg_homedir:
        opts += ['--homedir', gpg_homedir]
    gpg_log_file = s.get('gpg.log_file', '')
    if gpg_log_file:
        opts += ['--log_file', gpg_log_file]
    for _ in range(0, s.get('gpg.verbosity', 1)):
        opts.append('--verbose')
        
    if '--status-fd' not in opts_in:
        opts.extend(['--status-fd', '2'])
        
    opts += opts_in
    try:
        startupinfo = None
        if os.name == 'nt':
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW

        gpg_process = subprocess.Popen(opts,
                                       universal_newlines=False,
                                       stdin=subprocess.PIPE,
                                       stdout=subprocess.PIPE,
                                       stderr=subprocess.PIPE,
                                       startupinfo=startupinfo)
        result, error = gpg_process.communicate(input=data.encode())
        
        err_str = ""
        if error:
            err_str = error.decode('utf-8', errors='replace')
            sign_status = None
            is_decrypt = False
            for line in err_str.splitlines():
                if '[GNUPG:] BEGIN_DECRYPTION' in line:
                    is_decrypt = True
                    
                if '[GNUPG:] GOODSIG' in line:
                    parts = line.split(' ', 3)
                    signer = parts[3] if len(parts) > 3 else "Unknown"
                    sign_status = "GPG: 🟢 签名有效 ({})".format(signer)
                    break
                elif '[GNUPG:] BADSIG' in line:
                    parts = line.split(' ', 3)
                    signer = parts[3] if len(parts) > 3 else "Unknown"
                    sign_status = "GPG: 🔴 签名无效/被篡改! ({})".format(signer)
                    break
                elif '[GNUPG:] ERRSIG' in line:
                    sign_status = "GPG: 🟡 无法验证签名(无公钥)"
                    break
                    
            if sign_status:
                sublime.set_timeout(lambda: view.set_status('gpg_sign', sign_status), 0)
            elif is_decrypt:
                sublime.set_timeout(lambda: view.set_status('gpg_sign', 'GPG: ⚪ 未签名'), 0)

        if error and gpg_process.returncode != 0:
            clean_error = '\n'.join([line for line in err_str.splitlines() if not line.startswith('[GNUPG:]')])
            if clean_error.strip():
                sublime.set_timeout(lambda: panel(window, clean_error), 0)
        if gpg_process.returncode:
            return None
        return result.decode('utf-8', errors='replace').replace('\r\n', '\n').strip('\n')
    except IOError as e:
        sublime.set_timeout(lambda: panel(window, 'Error: %s' % e), 0)
        return None
    except OSError as e:
        sublime.set_timeout(lambda: panel(window, 'Error: %s' % e), 0)
        return None


def panel(window, message):
    '''
        Panel displays gpg's stderr at the bottom of the window.
    '''

    p = window.create_output_panel('gpg_message')
    p.run_command('gpg_message', {'message': message})
    p.show(p.size())
    window.run_command('show_panel', {'panel': 'output.gpg_message'})


class GpgReadmeCommand(sublime_plugin.TextCommand):
    '''
        Readme
    '''

    def run(self, edit):
        v = self.view
        v.set_name('GPG Auto: Readme')
        v.settings().set('syntax', 'Packages/Markdown/Markdown.sublime-syntax')
        v.insert(edit, 0, sublime.load_resource('Packages/GPG/README.md'))
        v.set_read_only(True)
        v.set_scratch(True)


class GpgMessageCommand(sublime_plugin.TextCommand):
    '''
        A helper command for panel.
    '''

    def run(self, edit, message):
        v = self.view
        v.insert(edit, v.size(), message)


class GpgAsyncCommand(sublime_plugin.TextCommand):
    '''
        A helper command to asynchronously replace all selections of the current document's
        content with new content.
    '''

    def run(self, edit, opts):
        v = self.view
        
        # Collect the selections and their content
        selections_data = []
        for selection in v.sel():
            selections_data.append((selection.begin(), selection.end(), v.substr(selection)))
            
        def worker():
            results = []
            for begin, end, text in selections_data:
                data = gpg(v, text, opts)
                if data:
                    results.append((begin, end, data))
            
            if results:
                # Dispatch back to the main thread to apply changes
                sublime.set_timeout(lambda: v.run_command("gpg_apply", {"changes": results}), 0)
                
        # Start the worker thread
        threading.Thread(target=worker).start()


class GpgApplyCommand(sublime_plugin.TextCommand):
    '''
        An internal helper command to apply asynchronous GPG results.
    '''

    def run(self, edit, changes):
        v = self.view
        # Apply changes in reverse order so that previous regions are not invalidated by size changes
        changes.sort(key=lambda x: x[0], reverse=True)
        for begin, end, data in changes:
            v.replace(edit, sublime.Region(begin, end), data)

class GpgAutoDecryptCommand(sublime_plugin.TextCommand):
    def run(self, edit):
        v = self.view
        text = v.substr(sublime.Region(0, v.size()))
        if "BEGIN PGP MESSAGE" not in text and "BEGIN PGP SIGNED MESSAGE" not in text:
            return
            
        is_clearsign = "BEGIN PGP SIGNED MESSAGE" in text
            
        def worker():
            data = gpg(v, text, ['--decrypt'])
            if data:
                def apply_and_scratch():
                    v.run_command("gpg_apply", {"changes": [(0, v.size(), data)]})
                    v.settings().set('gpg_last_save_cc', v.change_count())
                    v.set_scratch(True)
                    if is_clearsign:
                        v.settings().set('gpg_is_clearsign_mode', True)
                sublime.set_timeout(apply_and_scratch, 0)
                
        threading.Thread(target=worker).start()


class GpgEncryptFileCommand(sublime_plugin.TextCommand):
    '''
        Command to convert the current file into an .asc encrypted file.
        This simply appends .asc to the file name and invokes Sublime Text's native save.
        The auto_gpg_listener will automatically intercept the save and perform the encryption.
    '''

    def run(self, edit):
        v = self.view
        window = v.window()
        if not window:
            return

        filename = v.file_name()
        if filename:
            # File is already saved on disk
            if filename.endswith('.asc') or filename.endswith('.gpg'):
                sublime.status_message("GPG Auto: File is already an encrypted file.")
                return
            
            new_filename = filename + ".asc"
            v.retarget(new_filename)
            
            # Record the original file to delete after successful save
            v.settings().set('gpg_delete_original_file', filename)
            
            # Make the view dirty so that run_command('save') is forced to execute
            # and trigger the on_pre_save listener.
            v.insert(edit, 0, " ")
            v.erase(edit, sublime.Region(0, 1))
            
            # Run save asynchronously
            sublime.set_timeout(lambda: v.run_command("gpg_safe_save"), 0)
        else:
            # Unsaved memory buffer
            current_name = v.name()
            if not current_name:
                current_name = "untitled"
                
            if not current_name.endswith('.asc') and not current_name.endswith('.gpg'):
                new_name = current_name + ".asc"
                v.set_name(new_name)
            
            # Force dirty flag just in case the buffer is completely empty and clean
            v.insert(edit, 0, " ")
            v.erase(edit, sublime.Region(0, 1))
            
            # For unsaved files, calling "save" will automatically open the ST native save dialog
            sublime.set_timeout(lambda: v.run_command("gpg_safe_save"), 0)


class GpgToggleSignCommand(sublime_plugin.TextCommand):
    '''
        Command to trigger a signature on the next save for an encrypted file.
    '''
    
    def is_enabled(self):
        v = self.view
        filename = v.file_name()
        return filename is not None and (filename.endswith('.asc') or filename.endswith('.gpg'))

    def run(self, edit):
        v = self.view
        v.settings().set('gpg_sign_next_save', True)
        
        filename = v.file_name()
        if filename and not (filename.endswith('.asc') or filename.endswith('.gpg')):
            new_filename = filename + ".asc"
            v.retarget(new_filename)
            v.settings().set('gpg_delete_original_file', filename)
        
        # 强制制造脏标记以确保 run_command("save") 能触发 on_pre_save
        v.insert(edit, 0, " ")
        v.erase(edit, sublime.Region(0, 1))
        
        sublime.status_message("GPG Auto: 正在保存并附加签名...")
        sublime.set_timeout(lambda: v.run_command("gpg_safe_save"), 0)


class GpgClearsignDocumentCommand(sublime_plugin.TextCommand):
    '''
        Command to clearsign the current document transparently.
    '''

    def run(self, edit):
        v = self.view
        v.settings().set('gpg_is_clearsign_mode', True)
        
        filename = v.file_name()
        if filename and not (filename.endswith('.asc') or filename.endswith('.gpg')):
            new_filename = filename + ".asc"
            v.retarget(new_filename)
            v.settings().set('gpg_delete_original_file', filename)
        elif not filename:
            current_name = v.name() or "untitled"
            if not (current_name.endswith('.asc') or current_name.endswith('.gpg')):
                v.set_name(current_name + ".asc")
        
        # 强制制造脏标记以确保 run_command("save") 能触发 on_pre_save
        v.insert(edit, 0, " ")
        v.erase(edit, sublime.Region(0, 1))
        
        sublime.status_message("GPG Auto: 正在以透明明文模式保存并签名...")
        sublime.set_timeout(lambda: v.run_command("gpg_safe_save"), 0)

