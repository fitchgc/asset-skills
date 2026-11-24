import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from shared.asxlib.ui_server import WorkbenchServer

class RuntimeControlsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.server = WorkbenchServer(mode='asset_manager', output_dir=self.tmp.name, source_path=None, output_path=None, static_dir=None,
                                      metadata={'workspace_root': self.tmp.name})
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = 'http://127.0.0.1:' + str(self.server.server_address[1])

    def tearDown(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join(); self.tmp.cleanup()

    def request(self, path, body=None, auth=True):
        headers = {'Content-Type': 'application/json'}
        if auth: headers['X-Asx-Session-Token'] = self.server.session_token
        request = Request(self.base + path, headers=headers,
                          data=None if body is None else json.dumps(body).encode())
        try: response = urlopen(request)
        except HTTPError as error: response = error
        return response.status, json.load(response)

    def test_network_auth_failure_is_not_reported_as_missing_binding(self):
        from shared.asxlib.errors import AsxError
        with patch('shared.asxlib.ui_server.load_credential_context', return_value={'origin':'https://dev.plaiers.ai'}):
            with patch('shared.asxlib.ui_server.load_auth_session', side_effect=AsxError('offline',code='network_error')):
                self.assertEqual(self.server.get_auth_status()['auth_state'],'unavailable')
            with patch('shared.asxlib.ui_server.load_auth_session', side_effect=AsxError('missing',code='need_login',details={'reason_code':'missing_context'})):
                self.assertEqual(self.server.get_auth_status()['auth_state'],'needs_connection')

    def test_local_runtime_requires_session_and_explicit_download_consent(self):
        with patch('shared.asxlib.optional_deps.optional_runtime_status', return_value={'status':'not_prepared'}), \
             patch('shared.asxlib.runtime_env.prepare_runtime', return_value={'ok':True,'prepared':True}) as prepare:
            self.assertEqual(self.request('/api/runtime/status', auth=False)[0],401)
            status, data = self.request('/api/runtime/status')
            self.assertEqual(status,200); self.assertEqual(data['runtime']['status'],'not_prepared')
            self.assertEqual(self.request('/api/runtime/prepare', {'confirm_download':True},auth=False)[0],401)
            for body in [{}, {'confirm_download':False}, {'confirm_download':'true'}]:
                self.assertEqual(self.request('/api/runtime/prepare',body)[0],400)
            prepare.assert_not_called()
            self.assertEqual(self.request('/api/runtime/prepare',{'confirm_download':True})[0],200)
            prepare.assert_called_once_with()

if __name__ == '__main__': unittest.main()
