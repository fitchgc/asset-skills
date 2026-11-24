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
                data = self.server.get_auth_status()
                self.assertEqual(data['auth_state'],'needs_connection')
                self.assertEqual(data['state'],'unauthorized')
            with patch('shared.asxlib.ui_server.load_auth_session', side_effect=AsxError('bridge unavailable',code='need_login',details={'reason_code':'bridge_unavailable'})):
                data = self.server.get_auth_status()
                self.assertEqual(data['auth_state'],'unavailable')
                self.assertEqual(data['state'],'unavailable')
            with patch('shared.asxlib.ui_server.load_auth_session', side_effect=AsxError('slow bridge',code='need_login',details={'reason_code':'bridge_timeout'})):
                self.assertEqual(self.server.get_auth_status()['auth_state'],'unavailable')

            for message, expected in [
                ('Authorization expired or revoked; reconnect explicitly.', 'needs_connection'),
                ('Community token missing, expired or revoked.', 'needs_connection'),
                ('No binding for this Agent and site. Run device-login with the same --agent-id.', 'needs_connection'),
                ('JWT request failed (HTTP 401).', 'needs_connection'),
                ('JWT request failed (HTTP 404).', 'unavailable'),
                ('JWT request failed (HTTP 429).', 'unavailable'),
                ('JWT request failed (HTTP 503).', 'unavailable'),
                ('Invalid JWT response.', 'unavailable'),
                ('This site does not offer extension skill credentials.', 'unavailable'),
                ('an unexpected bridge error', 'unavailable'),
            ]:
                error = AsxError('bridge failure', code='need_login', details={'reason_code':'bridge_failed','bridge_error':message})
                with patch('shared.asxlib.ui_server.load_auth_session', side_effect=error):
                    self.assertEqual(self.server.get_auth_status()['auth_state'], expected, message)
            with patch('shared.asxlib.ui_server.load_auth_session', side_effect=AsxError('scope missing',code='scope_missing')):
                self.assertEqual(self.server.get_auth_status()['state'],'unauthorized')

    def test_identity_failure_keeps_transient_outage_separate_from_rejected_credentials(self):
        from shared.asxlib import ui_server
        from shared.asxlib.errors import AsxError
        session = type('Session', (), {'origin':'https://dev.plaiers.ai','base_url':'https://gateway.example',
                                      'asynx':{},'token':'test-token','token_header':'Authorization'})()
        cases = [
            (AsxError('network timed out', code='network_error'), 'unavailable', True),
            (AsxError('Asset Gateway rejected JWT', code='need_login', http_status=401,
                      details={'reason_code':'gateway_rejected_jwt'}), 'unauthorized', False),
        ]
        for error, state, authenticated in cases:
            with self.subTest(state=state), patch.object(ui_server, 'load_auth_session', return_value=session), \
                 patch.object(ui_server.AsynxClient, 'partner_identity', side_effect=error):
                data = self.server.get_auth_status()
                self.assertEqual(data['state'], state)
                self.assertEqual(data['auth_state'], 'unavailable' if state == 'unavailable' else 'needs_connection')
                self.assertEqual(data['authenticated'], authenticated)
                self.assertEqual(data['identity_error']['message'], error.message)

    def test_local_runtime_requires_session_and_explicit_download_consent(self):
        with patch('shared.asxlib.optional_deps.optional_runtime_status', return_value={'status':'not_prepared'}), \
             patch('shared.asxlib.runtime_env.prepare_runtime', return_value={'ok':True,'prepared':True}) as prepare:
            self.assertEqual(self.request('/api/runtime/status', auth=False)[0],401)
            status, data = self.request('/api/runtime/status')
            self.assertEqual(status,200); self.assertEqual(data['runtime']['status'],'not_prepared')
            self.assertEqual(self.request('/api/runtime/prepare', {'confirm_download':True},auth=False)[0],401)
            for body in [{}, {'confirm_download':False}, {'confirm_download':'true'}, {'user_confirmed':1}]:
                status, data = self.request('/api/runtime/prepare',body)
                self.assertEqual(status,409)
                self.assertEqual(data['error']['code'],'confirmation_required')
            prepare.assert_not_called()
            for body in [{'confirm_download':True}, {'user_confirmed':True}]:
                self.assertEqual(self.request('/api/runtime/prepare',body)[0],200)
            self.assertEqual(prepare.call_count,2)

if __name__ == '__main__': unittest.main()
