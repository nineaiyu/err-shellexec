# This is a Shell for Err plugins, use this to get started quickly.

from errbot import BotPlugin, botcmd
from os import listdir
from os.path import isfile, join
import logging
import hashlib
import subprocess

import threading
import time
import queue

import procrun

# Logger for the shell_exec command
log = logging.getLogger("shell_exec")
SCRIPT_PATH = '/opt/errbot/cmds'
SCRIPT_LOGS = '/var/log/errbot/shellexec'

def status_to_string(exit_code):
    if exit_code == 0:
        return "successfully"
    return "unsuccessfully"

def slack_upload(self, msg, buf):
    if msg.is_group:
        to_channel_id = msg.to.id
    else:
        to_channel_id = msg.to.channelid
        if to_channel_id.startswith('C'):
            log.debug("This is a divert to private msgage, sending it directly to the user.")
            to_channel_id = self.get_im_channel(self.username_to_userid(msg.to.username))
    self._bot.api_call('files.upload', data={
        'channels': to_channel_id,
        'content': buf,
        'filename': hashlib.sha224(buf.encode('utf-8')).hexdigest(),
        'filetype': 'text',
})

class ShellExec(BotPlugin):
    """
    Class that dynamically creates bot actions based on a set of shell scripts
    """
    min_err_version = '3.0.0'  # Optional, but recommended

    def __init__(self, bot,*args,**kwargs):
        """
        Constructor
        """
        super(ShellExec, self).__init__(bot,*args,**kwargs)
        self.dynamic_plugin = None

    def activate(self):
        """
        Activate this plugin,
        """
        super().activate()
        self._load_shell_commands()

    def deactivate(self):
        """
        Deactivate this plugin
        """
        super().deactivate()
        self._bot.remove_commands_from(self.dynamic_plugin)

    @botcmd(split_args_with=' ', admin_only=True)
    def fx_list_hosts(self, _, args):
        """
        Show all fx server.
        """
        pass

    @botcmd(admin_only=True)
    def fx_list_actions(self, msg, args):
        """
        Show all fx server.
        """
        pass

    @botcmd(admin_only=True)
    def fx_crypto(self, msg, args):
        """
        !crypto fx01 chat restart   or !crypto fx01 chat restart
        """
        self.log.debug("Unloading ShellExec Scripts%s  %s"%( msg, args))

        return ("Done unloading commands.")

    @botcmd
    def cmdunload(self, msg, args):
        """
        Remove the dynamically added shell commands and the ShellCmd object.
        """
        self.log.debug("Unloading ShellExec Scripts")
        if self.dynamic_plugin is not None:
            self._bot.remove_commands_from(self.dynamic_plugin)
        return ("Done unloading commands.")


    @botcmd
    def rehash(self, msg, args):
        """
        Remove the previous set of methods and add new ones based on the
        current set of scripts.
        """
        self.log.debug("Reloading ShellExec Scripts")
        yield "Checking for available commands."
        self._bot.remove_commands_from(self.dynamic_plugin)
        self.dynamic_plugin = None
        self._load_shell_commands()
        yield "Done loading commands."

    def _load_shell_commands(self):
        """
        Load the list of shell scripts from the SCRIPT_PATH, then call create_method
        on each script to add a dynamically created method for that shell script.
        Once done, generate an object out of a dictionary of the dynamically created
        methods and add that to the bot.
        """
        script_path = SCRIPT_PATH
        self.log.info("Loading scripts from {}".format(script_path))
        # Read the files
        files = [f for f in listdir(script_path) if isfile(join(script_path, f)) and f.endswith('.sh')]
        commands = {}

        # Create a method on the commands object for each script.
        for file in files:
            self.log.debug("Processing file [%s" % (file))
            file, _ = file.split(".")
            commands[file] = self._create_method(file)

        plugin_class = type("ShellCmd", (BotPlugin, ), commands)
        plugin_class.__errdoc__ = 'The ShellCmd plugin is created and managed by the ShellExec plugin.'
        plugin_class.command_path = SCRIPT_PATH
        plugin_class.command_logs_path = SCRIPT_LOGS
        plugin_class.slack_upload = slack_upload

        self.dynamic_plugin = plugin_class(self._bot)
        self.log.debug("Registering Dynamic Plugin: %s" % (self.dynamic_plugin))
        self._bot.inject_commands_from(self.dynamic_plugin)

    def _get_command_help(self, command_name):
        """
        Run the script with the --help option and capture the output to be used
        as its help text in chat.
        """
        os_cmd = join(SCRIPT_PATH, command_name + ".sh")
        log.debug("Getting help info for '{}'".format(os_cmd))
        return subprocess.check_output([os_cmd, "--help"])

    def _create_method(self, command_name):
        """
        Create a botcmd decorated method for our dynamic shell object
        """
        self.log.debug("Adding shell command '{}'".format(command_name))

        def new_method(self, msg, args, command_name=command_name):
            # Get who ran the command
            user = msg.frm.userid
            # The full command to run
            os_cmd = join(self.command_path, command_name + ".sh")
            q = queue.Queue()
            proc = procrun.ProcRun(os_cmd, self.command_path, self.command_logs_path, q)
            print("args: " + str(args))
            t = threading.Thread(target=procrun.ProcRun.run_async,
                args=(proc, user), kwargs={'arg_str':args})
            t.start()
            time.sleep(0.5)
            snippets = False
            while t.isAlive() or not q.empty():
                lines = []
                while not q.empty():
                    line = q.get()
                    if line is None:
                        break
                    lines.append(line.rstrip())
                while len(lines) > 0:
                    if len(lines) >= 25:
                        snippets = True
                    chunk = lines[:100]
                    if snippets:
                       self.slack_upload(msg,'\n'.join(chunk))
                    else:
                       buf = '\`\`\`' + '\n'.join(chunk) + '\`\`\`'
                       self.log.debug(buf)
                       yield buf
                    lines = lines[100:]
                time.sleep(2)
            t.join()
            yield "[{}] completed {}".format(command_name, status_to_string(proc.rc))

        self.log.debug("Updating metadata on command {} type {}".format(command_name, type(command_name)))
        new_method.__name__ = str(command_name)
        new_method.__doc__ = self._get_command_help(command_name)

        # Decorate the method
        return botcmd(new_method)
