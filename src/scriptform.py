#!/usr/bin/env python

# Todo:
#
#  - How does script_raw check the exitcode? Document this.
#  - Radio field type has no correct default value.
#  - Default values for input fields.
#  - If there are errors in the form, its values are empties.

import sys
import optparse
import os
import stat
import json
import BaseHTTPServer
from BaseHTTPServer import BaseHTTPRequestHandler
from SocketServer import ThreadingMixIn
import cgi
import re
import datetime
import subprocess
import base64
import tempfile
import hashlib


html_header = '''<html>
<head>
  <style>
    .btn {{ color: #FFFFFF; font-weight: bold; font-size: 0.90em;
           background-color: #1D98E4; border-color: #1D98E4; padding: 9px;
           border-radius: 4px; border-width: 0px; text-decoration: none;
           }}
    .error {{ color: #FF0000; }}
    *,body {{ font-family: sans-serif; }}
    h1 {{ color: #555555; text-align: center; margin: 32px auto 32px auto; }}
    pre {{ font-family: monospace; }}

    /* List of available forms */
    div.list {{ width: 50%; margin: 40px auto 0px auto; }}
    div.list li {{ font-size: 0.90em; list-style: none;
                  margin-bottom: 65px; }}
    div.list h2 {{ background-color: #E0E5E5;
                  border-radius: 3px; font-weight: bold;
                  padding: 10px; font-size: 1.2em; }}
    div.list p.form-description {{ margin-left: 25px; }}
    div.list a.form-link {{ margin-left: 25px; }}

    /* Form display */
    div.form {{ width: 50%; margin: 40px auto 0px auto; }}
    div.form h2 {{ font-weight: bold; background-color: #E0E5E5; padding: 25px;
                  border-radius: 10px; }}
    div.form p.form-description {{ font-size: 0.90em;
                                  margin: 40px 25px 65px 25px; }}
    div.form li {{ font-size: 0.90em; list-style: none; }}
    div.form p.form-field-title {{ margin-bottom: 0px; }}
    div.form p.form-field-input {{ margin-top: 0px; }}
    select,
    textarea,
    input[type=text],
    input[type=number],
    input[type=date],
    input[type=password],
    input[type=submit] {{ color: #606060; padding: 9px; border-radius: 4px;
                         border: 1px solid #D0D0D0;
                         background-color: #F9F9F9;}}
    input[type=submit] {{ color: #FFFFFF; font-weight: bold;
                         background-color: #1D98E4; border-color: #1D98E4}}
    textarea {{ font-family: monospace; }}
    /* Result display */
    div.result {{ width: 50%; margin: 40px auto 0px auto; }}
    div.result h2 {{ background-color: #E0E5E5; border-radius: 3px;
                    font-weight: bold; padding: 10px; }}
    div.result div.result-result {{ margin-left: 25px; }}
    div.result ul {{ margin-top: 64px; padding-left: 0px; }}
    div.result ul li {{ list-style: none; float: left; margin-right: 20px;
                        font-size: 0.90em; }}
    div.result ul.nav {{ margin-bottom: 128px; }}
  </style>
</head>
<body>
  <h1>{title}</h1>
  <div class="page">
'''

html_footer = '''
  </div>
</body>
</html>
'''

html_list = '''
{header}
<div class="list">
  {form_list}
</div>
{footer}
'''

html_form = '''
{header}
<div class="form">
  <h2 class="form-title">{title}</h2>
  <p class="form-description">{description}</p>
  <form action="submit" method="post" enctype="multipart/form-data">
    <input type="hidden" name="form_name" value="{name}" />
    <ul>
        {fields}
        <li><input type="submit" value="{submit_title}" /></li>
    </ul>
  </form>
</div>
{footer}
'''

html_submit_response = '''
{header}
<div class="result">
  <h2 class="result-title">{title}</h2>
  <h3 class="result-subtitle">Result</h3>
  <div class="result-result">{msg}</div>
  <ul class="nav">
    <li>
      <a class="back-list btn" href=".">Back to the list</a>
    </li>
    <li>
      <a class="back-form btn" href="form?form_name={form_name}">
        Back to the form
      </a>
    </li>
  </ul>
</div>
{footer}
'''


class FormDefinition:
    """
    FormDefinition holds information about a single form and provides methods
    for validation of the form values.
    """
    def __init__(self, name, title, description, fields, script=None,
                 output='escaped', submit_title="Submit",
                 allowed_users=None):
        self.name = name
        self.title = title
        self.description = description
        self.fields = fields
        self.script = script
        self.output = output
        self.submit_title = submit_title
        self.allowed_users = allowed_users

    def get_field(self, field_name):
        for field in self.fields:
            if field['name'] == field_name:
                return field

    def validate(self, form_values):
        """
        Validate all relevant fields for this form against form_values.
        """
        errors = {}
        values = form_values.copy()

        # First make sure all required fields are there
        for field in self.fields:
            if 'required' in field and \
               field['required'] is True and \
               field['name'] not in form_values:
                errors.setdefault(field['name'], []).append(
                    "This field is required"
                )

        # Validate the field values, possible casting them to the correct type.
        for field in self.fields:
            field_name = field['name']
            if field_name == 'form_name':
                continue
            try:
                v = self.validate_field(field_name, form_values)
                if v is not None:
                    values[field_name] = v
            except Exception, e:
                errors.setdefault(field_name, []).append(str(e))

        return (errors, values)

    def validate_field(self, field_name, form_values):
        """
        Validate a field in this form.
        """
        # Find field definition by iterating through all the fields.
        field_def = self.get_field(field_name)
        if not field_def:
            raise KeyError("Unknown field: {0}".format(field_name))

        field_type = field_def['type']
        validate_cb = getattr(self, 'validate_{0}'.format(field_type), None)
        return validate_cb(field_def, form_values)

    def validate_string(self, field_def, form_values):
        value = form_values[field_def['name']]
        maxlen = field_def.get('maxlen', None)
        minlen = field_def.get('minlen', None)

        if minlen is not None and len(value) < minlen:
            raise Exception("Minimum length is {0}".format(minlen))
        if maxlen is not None and len(value) > maxlen:
            raise Exception("Maximum length is {0}".format(maxlen))

        return value

    def validate_integer(self, field_def, form_values):
        value = form_values[field_def['name']]
        max = field_def.get('max', None)
        min = field_def.get('min', None)

        try:
            value = int(value)
        except ValueError:
            raise Exception("Must be an integer number")

        if min is not None and value < min:
            raise Exception("Minimum value is {0}".format(min))
        if max is not None and value > max:
            raise Exception("Maximum value is {0}".format(max))

        return int(value)

    def validate_float(self, field_def, form_values):
        value = form_values[field_def['name']]
        max = field_def.get('max', None)
        min = field_def.get('min', None)

        try:
            value = float(value)
        except ValueError:
            raise Exception("Must be an real (float) number")

        if min is not None and value < min:
            raise Exception("Minimum value is {0}".format(min))
        if max is not None and value > max:
            raise Exception("Maximum value is {0}".format(max))

        return float(value)

    def validate_date(self, field_def, form_values):
        value = form_values[field_def['name']]
        max = field_def.get('max', None)
        min = field_def.get('min', None)

        try:
            value = datetime.datetime.strptime(value, '%Y-%m-%d').date()
        except ValueError:
            raise Exception("Invalid date, must be in form YYYY-MM-DD")

        if min is not None:
            if value < datetime.datetime.strptime(min, '%Y-%m-%d').date():
                raise Exception("Minimum value is {0}".format(min))
        if max is not None:
            if value > datetime.datetime.strptime(max, '%Y-%m-%d').date():
                raise Exception("maximum value is {0}".format(max))

        return value

    def validate_radio(self, field_def, form_values):
        value = form_values[field_def['name']]
        if not value in [o[0] for o in field_def['options']]:
            raise ValueError(
                "Invalid value for radio button: {0}".format(value))
        return value

    def validate_select(self, field_def, form_values):
        value = form_values[field_def['name']]
        if not value in [o[0] for o in field_def['options']]:
            raise ValueError(
                "Invalid value for dropdown: {0}".format(value))
        return value

    def validate_text(self, field_def, form_values):
        value = form_values[field_def['name']]
        minlen = field_def.get('minlen', None)
        maxlen = field_def.get('maxlen', None)

        if minlen is not None:
            if len(value) < minlen:
                raise Exception("minimum length is {0}".format(minlen))

        if maxlen is not None:
            if len(value) > maxlen:
                raise Exception("maximum length is {0}".format(maxlen))

        return value

    def validate_password(self, field_def, form_values):
        value = form_values[field_def['name']]
        minlen = field_def.get('minlen', None)

        if minlen is not None:
            if len(value) < minlen:
                raise Exception("minimum length is {0}".format(minlen))

        return value

    def validate_file(self, field_def, form_values):
        value = form_values[field_def['name']]
        field_name = field_def['name']
        upload_fname = form_values['{0}__name'.format(field_name)]
        upload_fname_ext = os.path.splitext(upload_fname)[-1].lstrip('.')
        extensions = field_def.get('extensions', None)

        if extensions is not None and upload_fname_ext not in extensions:
            raise Exception("Only file types allowed: {0}".format(','.join(extensions)))

        return value


class ThreadedHTTPServer(ThreadingMixIn, BaseHTTPServer.HTTPServer):
    pass


class WebSrv:
    """
    Very basic web server.
    """
    def __init__(self, request_handler, listen_addr='', listen_port=80):
        httpd = ThreadedHTTPServer((listen_addr, listen_port), request_handler)
        httpd.serve_forever()


class WebAppHandler(BaseHTTPRequestHandler):
    """
    Basic web server request handler. Handles GET and POST requests. This class
    should be extended with methods (starting with 'h_') to handle the actual
    requests. If no path is set, it dispatches to the 'index' or 'default'
    method.
    """
    def do_GET(self):
        self.call(*self.parse(self.path))

    def do_POST(self):
        form_values = cgi.FieldStorage(
            fp=self.rfile,
            headers=self.headers,
            environ={'REQUEST_METHOD': 'POST'})
        self.call(self.path.strip('/'), params={'form_values': form_values})

    def parse(self, reqinfo):
        if '?' in reqinfo:
            path, params = reqinfo.split('?', 1)
            params = dict(
                [p.split('=', 1) for p in params.split('&') if '=' in p]
            )
            return (path.strip('/'), params)
        else:
            return (self.path.strip('/'), {})

    def call(self, path, params):
        """
        Find a method to call on self.app_class based on `path` and call it.
        The method that's called is in the form 'h_<PATH>'. If no path was
        given, it will try to call the 'index' method. If no method could be
        found but a `default` method exists, it is called. Otherwise 404 is
        sent.

        Methods should take care of sending proper headers and content
        themselves using self.send_response(), self.send_header(),
        self.end_header() and by writing to self.wfile.
        """
        method_name = 'h_{0}'.format(path)
        method_cb = None
        try:
            if hasattr(self, method_name) and \
               callable(getattr(self, method_name)):
                method_cb = getattr(self, method_name)
            elif path == '' and hasattr(self, 'index'):
                method_cb = self.index
            elif hasattr(self, 'default'):
                method_cb = self.default
            else:
                self.send_error(404, "Not found")
                return
            method_cb(**params)
        except Exception, e:
            self.send_error(500, "Internal server error")
            raise


class ScriptFormWebApp(WebAppHandler):
    """
    This class is a request handler for WebSrv.
    """
    def index(self):
        return self.h_list()

    def auth(self):
        """
        Verify that the user is authenticated. This is required if the form
        definition contains a 'users' field. Returns True if the user is
        validated. Otherwise, returns False and sends 401 HTTP back to the
        client.
        """
        self.username = None

        # If a 'users' element was present in the form configuration file, the
        # user must be authenticated.
        if self.scriptform.users:
            authorized = False
            auth_header = self.headers.getheader("Authorization")
            if auth_header is not None:
                auth_realm, auth_unpw = auth_header.split(' ', 1)
                username, password = base64.decodestring(auth_unpw).split(":")
                pw_hash = hashlib.sha256(password).hexdigest()
                # Validate the username and password
                if username in self.scriptform.users and \
                   pw_hash == self.scriptform.users[username]:
                    self.username = username
                    authorized = True

            if not authorized:
                # User is not authenticated. Send authentication request.
                self.send_response(401)
                self.send_header("WWW-Authenticate", 'Basic realm="Private Area"')
                self.end_headers()
                return False
        return True

    def h_list(self):
        if not self.auth():
            return

        h_form_list = []
        for form_name, form_def in self.scriptform.forms.items():
            if form_def.allowed_users is not None and \
               self.username not in form_def.allowed_users:
                continue # User is not allowed to run this form
            h_form_list.append('''
              <li>
                <h2 class="form-title">{title}</h2>
                <p class="form-description">{description}</p>
                <a class="form-link btn" href="./form?form_name={name}">
                  {title}
                </a>
              </li>
            '''.format(title=form_def.title,
                       description=form_def.description,
                       name=form_name)
            )

        output = html_list.format(
            header=html_header.format(title=self.scriptform.title),
            footer=html_footer,
            form_list=''.join(h_form_list)
        )
        self.send_response(200)
        self.send_header('Content-type', 'text/html')
        self.end_headers()
        self.wfile.write(output)

    def h_form(self, form_name, errors={}):
        if not self.auth():
            return

        field_tpl = {
            "string": '<input {0} type="text" name="{1}" />',
            "number": '<input {0} type="number" min="{1}" max="{2}" name="{3}" />',
            "integer": '<input {0} type="number" min="{1}" max="{2}" name="{3}" />',
            "float": '<input {0} type="number" min="{1}" max="{2}" step="any" name="{3}" />',
            "date": '<input {0} type="date" name="{1}" />',
            "file": '<input {0} type="file" name="{1}" />',
            "password": '<input {0} type="password" min="{1}" name="{2}" />',
            "text": '<textarea {0} name="{1}" rows="{2}" cols="{3}"></textarea>',
            "select": '<option value="{0}">{1}</option>',
            "radio": '<input checked type="radio" name="{0}" value="{1}">{2}<br/>',
        }

        def render_field(field, errors):
            tpl = field_tpl[field['type']]

            required = ''
            if field.get('required', None):
                required='required'

            if field['type'] == 'string':
                input = tpl.format(required, field['name'])
            elif field['type'] == 'number' or \
                    field['type'] == 'integer' or \
                    field['type'] == 'float':
                input = tpl.format(required, field.get('min', ''),
                                   field.get('max', ''),
                                   field['name'])
            elif field['type'] == 'date':
                input = tpl.format(required, field['name'])
            elif field['type'] == 'file':
                input = tpl.format(required, field['name'])
            elif field['type'] == 'password':
                input = tpl.format(required, field.get('minlen', ''), field['name'])
            elif field['type'] == 'radio':
                input = ''.join(
                    [
                        tpl.format(field['name'], o[0], o[1])
                        for o in field['options']
                    ]
                )
            elif field['type'] == 'text':
                rows = field.get('rows', 5)
                cols = field.get('cols', 80)
                input = tpl.format(
                    required,
                    field['name'],
                    rows,
                    cols
                )
            elif field['type'] == 'select':
                options = ''.join([
                        tpl.format(o[0], o[1]) for o in field['options']
                    ]
                )
                input = '<select {0} name="{1}">{2}</select>'.format(required, field['name'], options)
            else:
                raise ValueError("Unsupported field type: {0}".format(
                    field['type'])
                )

            return ('''
              <li>
                <p class="form-field-title">{title}</p>
                <p class="form-field-input">{input} <span class="error">{errors}</span></p>
              </li>
            '''.format(
                    title=field['title'],
                    input=input,
                    errors=', '.join(errors)
                )
            )

        form_def = self.scriptform.get_form(form_name)
        if form_def.allowed_users is not None and \
           self.username not in form_def.allowed_users:
            raise Exception("Not authorized")

        html_errors = ''
        if errors:
            html_errors = '<ul>'
            for error in errors:
                html_errors += '<li class="error">{0}</li>'.format(error)
            html_errors += '</ul>'

        output = html_form.format(
            header=html_header.format(title=self.scriptform.title),
            footer=html_footer,
            title=form_def.title,
            description=form_def.description,
            errors=html_errors,
            name=form_def.name,
            fields=''.join([render_field(f, errors.get(f['name'], [])) for f in form_def.fields]),
            submit_title=form_def.submit_title
        )
        self.send_response(200)
        self.send_header('Content-type', 'text/html')
        self.end_headers()
        self.wfile.write(output)

    def h_submit(self, form_values):
        if not self.auth():
            return

        form_name = form_values.getfirst('form_name', None)
        form_def = self.scriptform.get_form(form_name)
        if form_def.allowed_users is not None and \
           self.username not in form_def.allowed_users:
            raise Exception("Not authorized")

        # Convert FieldStorage to a simple dict, because we're not allowd to
        # add items to it. For normal fields, the form field name becomes the
        # key and the value becomes the field value. For file upload fields, we
        # stream the uploaded file to a temp file and then put the temp file in
        # the destination dict. We also add an extra field with the originally
        # uploaded file's name.
        values = {}
        tmp_files = []
        for field_name in form_values:
            field = form_values[field_name]
            if field.filename:
                # Field is an uploaded file. Stream it to a temp file
                tmpfile = tempfile.mktemp(prefix="scriptform_")
                f = file(tmpfile, 'w')
                while True:
                    buf = field.file.read(1024 * 16)
                    if not buf:
                        break
                    f.write(buf)
                f.close()
                field.file.close()

                tmp_files.append(tmpfile) # For later cleanup
                values[field_name] = tmpfile
                values['{0}__name'.format(field_name)] = field.filename
            else:
                # Field is a normal form field. Store its value.
                values[field_name] = form_values.getfirst(field_name, None)

        # Validate the form values
        form_errors, form_values = form_def.validate(values)

        if not form_errors:
            # Call user's callback. If a result is returned, we wrap its output
            # in some nice HTML. If no result is returned, the output was raw
            # and the callback should have written its own response to the
            # self.wfile filehandle.
            result = self.scriptform.callback(form_name, form_values, self.wfile)
            if result:
                if result['exitcode'] != 0:
                    msg = '<span class="error">{0}</span>'.format(cgi.escape(result['stderr']))
                else:
                    if form_def.output == 'escaped':
                        msg = '<pre>{0}</pre>'.format(cgi.escape(result['stdout']))
                    else:
                        msg = result['stdout']

                output = html_submit_response.format(
                    header=html_header.format(title=self.scriptform.title),
                    footer=html_footer,
                    title=form_def.title,
                    form_name=form_def.name,
                    msg=msg,
                )
                self.send_response(200)
                self.send_header('Content-type', 'text/html')
                self.end_headers()
                self.wfile.write(output)
        else:
            # Form had errors
            self.h_form(form_name, form_errors)

        # Clean up uploaded files
        for file_name in tmp_files:
            if os.path.exists(file_name):
                os.unlink(file_name)

class ScriptForm:
    """
    'Main' class that orchestrates parsing the Form definition file
    `config_file`, hooking up callbacks and running the webserver.
    """
    def __init__(self, config_file, callbacks={}):
        self.forms = {}
        self.callbacks = {}
        self.title = 'ScriptForm Actions'
        self.users = None
        self.basepath = os.path.realpath(os.path.dirname(config_file))

        self._load_config(config_file)
        for form_name, cb in callbacks.items():
            self.callbacks[form_name] = cb

        # Validate scripts
        for form_name, form_def in self.forms.items():
            if form_def.script:
                if not stat.S_IXUSR & os.stat(form_def.script)[stat.ST_MODE]:
                    raise Exception("{0} is not executable".format(form_def.script))
            else:
                if not form_name in self.callbacks:
                    raise Exception("No script or callback registered for '{0}'".format(form_name))

    def _load_config(self, path):
        config = json.load(file(path, 'r'))
        if 'title' in config:
            self.title = config['title']
        if 'users' in config:
            self.users = config['users']
        for form_name, form in config['forms'].items():
            if 'script' in form:
                script = os.path.join(self.basepath, form['script'])
            else:
                script = None
            self.forms[form_name] = \
                FormDefinition(form_name,
                               form['title'],
                               form['description'],
                               form['fields'],
                               script,
                               output=form.get('output', 'escaped'),
                               submit_title=form.get('submit_title', None),
                               allowed_users=form.get('allowed_users', None))

    def get_form(self, form_name):
        return self.forms[form_name]

    def callback(self, form_name, form_values, output_fh=None):
        form = self.get_form(form_name)
        if form.script:
            return self.callback_script(form, form_values, output_fh)
        else:
            return self.callback_python(form, form_values, output_fh)

    def callback_script(self, form, form_values, output_fh=None):
        # Pass form values to the script through the environment as strings.
        env = os.environ.copy()
        for k, v in form_values.items():
            env[k] = str(v)

        if form.output == 'raw':
            p = subprocess.Popen(form.script, shell=True, stdout=output_fh,
                                 stderr=output_fh, env=env)
            stdout, stderr = p.communicate(input)
            return None
        else:
            p = subprocess.Popen(form.script, shell=True, stdin=subprocess.PIPE,
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                 env=env)
            stdout, stderr = p.communicate()
            return {
                'stdout': stdout,
                'stderr': stderr,
                'exitcode': p.returncode
            }

    def callback_python(self, form, form_values, output_fh=None):
        pass

    def run(self, listen_addr='0.0.0.0', listen_port=80):
        ScriptFormWebApp.scriptform = self
        ScriptFormWebApp.callbacks = self.callbacks
        WebSrv(ScriptFormWebApp, listen_addr=listen_addr, listen_port=listen_port)


def main_generate_pw(parser, options, args):
    import getpass
    plain_pw = getpass.getpass()
    if not plain_pw == getpass.getpass('Repeat password: '):
        sys.stderr.write("Passwords do not match.\n")
        sys.exit(1)
    print hashlib.sha256(plain_pw).hexdigest()
    sys.exit(0)

def main_serve(parser, options, args):
    if len(args) < 1:
        parser.error("Insufficient number of arguments")

    sf = ScriptForm(args[0])
    sf.run(listen_port=options.port)

if __name__ == "__main__":
    usage = [
        sys.argv[0] + " [option] <form_definition.json>",
        "       " + sys.argv[0] + " --generate-pw",
    ]
    parser = optparse.OptionParser()
    parser.set_usage('\n'.join(usage))

    parser.add_option("-g", "--generate-pw", dest="generate_pw", action="store_true", default=False, help="Generate password")
    parser.add_option("-p", "--port", dest="port", action="store", type="int", default=80, help="Port to listen on")

    (options, args) = parser.parse_args()
    if options.generate_pw:
        main_generate_pw(parser, options, args)
    else:
        main_serve(parser, options, args)
