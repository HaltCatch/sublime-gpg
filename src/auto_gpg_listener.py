import sublime
import sublime_plugin
import os

from .text_commands import gpg

def get_encrypt_opts(view):
    s = sublime.load_settings('gpg.sublime-settings')
    recipients = s.get('gpg.recipients')
    
    if view and view.settings().get('gpg_is_clearsign_mode', False):
        return ['--clearsign']
        
    opts = ['--encrypt']
    
    if view and view.settings().get('gpg_sign_next_save', False):
        opts.append('--sign')
        
    if recipients:
        import re
        recipients = re.split(';|,| ', recipients)
        for r in recipients:
            if r.strip():
                opts.extend(['--recipient', r.strip()])
    else:
        opts = ['--symmetric']
    return opts

def is_gpg_view(view):
    filename = view.file_name() or view.name() or ''
    return filename.lower().endswith(('.asc', '.gpg'))


class GpgSafeSaveCommand(sublime_plugin.TextCommand):
    """Run encryption before invoking a save that cannot be cancelled by a hook."""

    def run(self, edit, native_args=None, target=None):
        v = self.view
        args = dict(native_args or {})
        if target is None and (not v.file_name() or args.get('prompt')):
            filename = v.file_name() or v.name() or 'untitled'
            sublime.save_dialog(
                lambda path: v.run_command('gpg_safe_save', {
                    'native_args': native_args or {}, 'target': path
                }) if path else None,
                directory=os.path.dirname(filename),
                name=os.path.basename(filename))
            return

        original_filename = v.file_name()
        filename = target or original_filename
        protected = is_gpg_view(v) or filename.lower().endswith(('.asc', '.gpg'))
        text = v.substr(sublime.Region(0, v.size()))
        encrypted = (text.startswith('-----BEGIN PGP MESSAGE-----') or
                     text.startswith('-----BEGIN PGP SIGNED MESSAGE-----'))
        if protected and not encrypted:
            try:
                data = gpg(v, text, get_encrypt_opts(v))
            except Exception as exc:
                data = None
                print('GPG Auto: encryption failed:', exc)
            if not data:
                v.set_scratch(False)
                sublime.status_message('GPG: 加密失败或已取消；未保存，原文件保持不变。')
                return

            remember_plaintext(v, text)
            v.run_command('gpg_apply', {'changes': [(0, v.size(), data)]})

        # The filename is chosen and encryption succeeded before any disk write.
        if target:
            v.retarget(target)
        args.pop('prompt', None)
        args['async'] = False
        try:
            v.run_command('save', args)
        finally:
            # on_post_save consumes this only after a successful native write.
            if v.settings().has('gpg_plaintext_backup'):
                restore_plaintext(v)
                if original_filename and target:
                    v.retarget(original_filename)
                v.set_scratch(False)


class GpgSafeSaveAllCommand(sublime_plugin.WindowCommand):
    def run(self):
        for view in self.window.views():
            if view.is_dirty():
                if is_gpg_view(view):
                    view.run_command('gpg_safe_save')
                else:
                    view.run_command('save')


def remember_plaintext(view, text):
    settings = view.settings()
    settings.set('gpg_plaintext_backup', text)
    settings.set('gpg_restore_atomic', settings.get('atomic_save', True))
    viewport = view.viewport_position()
    settings.set('gpg_viewport_x', viewport[0])
    settings.set('gpg_viewport_y', viewport[1])
    settings.set('gpg_sel_backup', [[s.a, s.b] for s in view.sel()])


def restore_plaintext(view):
    settings = view.settings()
    text = settings.get('gpg_plaintext_backup')
    if settings.has('gpg_restore_filename'):
        view.retarget(settings.get('gpg_restore_filename'))
        settings.erase('gpg_restore_filename')
    view.run_command('gpg_apply', {'changes': [(0, view.size(), text)]})
    settings.erase('gpg_plaintext_backup')
    if settings.has('gpg_sel_backup'):
        selections = settings.get('gpg_sel_backup')
        view.sel().clear()
        for selection in selections:
            view.sel().add(sublime.Region(selection[0], selection[1]))
        settings.erase('gpg_sel_backup')
    if settings.has('gpg_viewport_x'):
        view.set_viewport_position((settings.get('gpg_viewport_x'), settings.get('gpg_viewport_y')), False)
        settings.erase('gpg_viewport_x')
        settings.erase('gpg_viewport_y')
    if settings.has('gpg_restore_atomic'):
        view.settings().set('atomic_save', settings.get('gpg_restore_atomic'))
        settings.erase('gpg_restore_atomic')


class GpgAutoEventListener(sublime_plugin.EventListener):
    def is_gpg_file(self, view):
        return is_gpg_view(view)

    def on_query_context(self, view, key, operator, operand, match_all):
        if key == 'gpg_protected_view':
            protected = self.is_gpg_file(view)
            return protected == operand if operator == sublime.OP_EQUAL else protected != operand

    def on_window_command(self, window, command_name, args):
        view = window.active_view()
        if command_name == 'prompt_save_as' and view and self.is_gpg_file(view):
            return ('gpg_safe_save', {'native_args': {'prompt': True}})
        if command_name == 'save_all' and any(is_gpg_view(v) for v in window.views()):
            return ('gpg_safe_save_all', {})

    def on_load_async(self, view):
        if self.is_gpg_file(view):
            view.run_command('gpg_auto_decrypt')

    def on_pre_save(self, view):
        # Native save bypasses command listeners (menus, plugins and close-save).
        # Keep this guard even though normal shortcuts use gpg_safe_save.
        if not self.is_gpg_file(view) or view.settings().has('gpg_plaintext_backup'):
            return
        text = view.substr(sublime.Region(0, view.size()))
        if text.startswith(('-----BEGIN PGP MESSAGE-----', '-----BEGIN PGP SIGNED MESSAGE-----')):
            return
        try:
            data = gpg(view, text, get_encrypt_opts(view))
        except Exception as exc:
            data = None
            print('GPG Auto: encryption failed:', exc)
        remember_plaintext(view, text)
        if not data:
            # Native save cannot be cancelled here. Discard an empty write
            # through the OS null device, then restore the original target and
            # dirty edits in on_post_save. Never open or overwrite the target.
            view.settings().set('gpg_save_failed', True)
            view.settings().set('gpg_restore_filename', view.file_name())
            view.settings().set('atomic_save', False)
            view.retarget(os.devnull)
            data = ''
        view.run_command('gpg_apply', {'changes': [(0, view.size(), data)]})

    def on_post_save(self, view):
        if self.is_gpg_file(view) or view.settings().has('gpg_plaintext_backup'):
            if view.settings().has('gpg_plaintext_backup'):
                restore_plaintext(view)
                if view.settings().get('gpg_save_failed', False):
                    view.settings().erase('gpg_save_failed')
                    view.set_scratch(False)
                    sublime.status_message('GPG: 加密失败或已取消；保留原磁盘内容及未保存的编辑。')
                    return

                # 利用 set_scratch 和 change_count 完美隐藏未保存的小圆点并取消关闭提示
                sublime.set_timeout(lambda: view.settings().set('gpg_last_save_cc', view.change_count()), 0)
                view.set_scratch(True)
                
                # 删除原明文文件 (仅在成功加密并落盘后)
                if view.settings().has('gpg_delete_original_file'):
                    orig_file = view.settings().get('gpg_delete_original_file')
                    if orig_file and orig_file != view.file_name():
                        try:
                            if os.path.exists(orig_file):
                                os.remove(orig_file)
                        except Exception as e:
                            print("GPG Auto: Could not delete original file:", e)
                    view.settings().erase('gpg_delete_original_file')
                    
                # 更新签名状态，如果刚刚保存时执行了签名，刷新状态栏让用户放心
                if view.settings().get('gpg_sign_next_save', False) or view.settings().get('gpg_is_clearsign_mode', False):
                    new_status = 'GPG: 🟢 已签名 (刚刚保存)'
                    view.set_status('gpg_sign', new_status)
                    view.settings().set('gpg_saved_valid_status', new_status)
                    
                    # 消费掉单次签名标志
                    view.settings().set('gpg_sign_next_save', False)
                    view.erase_status('gpg_sign_pending')
                else:
                    new_status = 'GPG: ⚪ 未签名'
                    view.set_status('gpg_sign', new_status)
                    view.settings().set('gpg_saved_valid_status', new_status)

    def on_modified(self, view):
        if self.is_gpg_file(view):
            last_cc = view.settings().get('gpg_last_save_cc')
            if last_cc is not None:
                if view.change_count() != last_cc:
                    # 有新编辑，恢复脏标记（显示圆点和关闭提示）
                    view.set_scratch(False)
                    
                    # 实时使签名失效
                    current_status = view.get_status('gpg_sign')
                    if current_status and '🟢' in current_status:
                        view.settings().set('gpg_saved_valid_status', current_status)
                        view.set_status('gpg_sign', 'GPG: ⚪ 未签名')
                else:
                    # 撤销回了刚保存的状态，再次隐藏圆点
                    view.set_scratch(True)
                    
                    # 恢复原来的签名状态
                    if view.settings().has('gpg_saved_valid_status'):
                        view.set_status('gpg_sign', view.settings().get('gpg_saved_valid_status'))

