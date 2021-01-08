# Copyright 2016 Canonical Ltd
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#  http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from unittest.mock import MagicMock, patch, call, ANY
import os
import json
import cinder_utils as utils

from test_utils import (
    CharmTestCase,
)

# Need to do some early patching to get the module loaded.
_register_configs = utils.register_configs
utils.register_configs = MagicMock()
import cinder_hooks as hooks  # noqa
utils.register_configs = _register_configs

TO_PATCH = [
    # cinder_utils
    'ensure_ceph_keyring',
    'register_configs',
    'restart_map',
    'scrub_old_style_ceph',
    'is_request_complete',
    'send_request_if_needed',
    'CONFIGS',
    'CEPH_CONF',
    'ceph_config_file',
    'ceph_replication_device_config_file',
    # charmhelpers.core.hookenv
    'application_name',
    'config',
    'relation_ids',
    'relation_set',
    'service_name',
    'service_restart',
    'log',
    'leader_get',
    'leader_set',
    'is_leader',
    # charmhelpers.core.host
    'apt_install',
    'apt_update',
    # charmhelpers.contrib.hahelpers.cluster_utils
    'execd_preinstall',
    'CephSubordinateContext',
    'delete_keyring',
    'remove_alternative',
    'status_set',
    'os_application_version_set',
    'send_application_name',
]


class TestCinderHooks(CharmTestCase):
    def setUp(self):
        super(TestCinderHooks, self).setUp(hooks, TO_PATCH)
        self.config.side_effect = self.test_config.get

    @patch('charmhelpers.core.hookenv.config')
    def test_install(self, mock_config):
        hooks.hooks.execute(['hooks/install'])
        self.assertTrue(self.execd_preinstall.called)
        self.assertTrue(self.apt_update.called)
        self.apt_install.assert_called_with(['ceph-common'], fatal=True)

    @patch('charmhelpers.core.hookenv.config')
    @patch('os.mkdir')
    def test_ceph_joined(self, mkdir, mock_config):
        '''It correctly prepares for a ceph changed hook'''
        with patch('os.path.isdir') as isdir:
            isdir.return_value = False
            hooks.hooks.execute(['hooks/ceph-relation-joined'])
            mkdir.assert_called_with('/etc/ceph')
            self.send_application_name.assert_called_with()

    @patch('charmhelpers.core.hookenv.config')
    def test_ceph_changed_no_key(self, mock_config):
        '''It does nothing when ceph key is not available'''
        self.CONFIGS.complete_contexts.return_value = ['']
        hooks.hooks.execute(['hooks/ceph-relation-changed'])
        m = 'ceph relation incomplete. Peer not ready?'
        self.log.assert_called_with(m)

    @patch.object(hooks, 'get_ceph_request')
    @patch('charmhelpers.core.hookenv.config')
    def test_ceph_changed(self, mock_config, mock_get_ceph_request):
        '''It ensures ceph assets created on ceph changed'''
        # confirm ValueError is caught and logged
        self.is_request_complete.side_effect = ValueError
        hooks.hooks.execute(['hooks/ceph-relation-changed'])
        self.assertFalse(self.CONFIGS.write_all.called)
        self.assertTrue(self.log.called)
        self.is_request_complete.side_effect = None
        # normal operation
        self.is_request_complete.return_value = True
        self.CONFIGS.complete_contexts.return_value = ['ceph']
        self.service_name.return_value = 'cinder'
        self.ensure_ceph_keyring.return_value = True
        hooks.hooks.execute(['hooks/ceph-relation-changed'])
        self.ensure_ceph_keyring.assert_called_with(service='cinder',
                                                    user='cinder',
                                                    group='cinder')
        self.assertTrue(self.CONFIGS.write_all.called)

    @patch.object(hooks, 'get_ceph_request')
    @patch('charmhelpers.core.hookenv.config')
    def test_ceph_changed_newrq(self, mock_config, mock_get_ceph_request):
        '''It ensures ceph assets created on ceph changed'''
        mock_get_ceph_request.return_value = 'cephreq'
        self.is_request_complete.return_value = False
        self.CONFIGS.complete_contexts.return_value = ['ceph']
        self.service_name.return_value = 'cinder'
        self.ensure_ceph_keyring.return_value = True
        hooks.hooks.execute(['hooks/ceph-relation-changed'])
        self.ensure_ceph_keyring.assert_called_with(service='cinder',
                                                    user='cinder',
                                                    group='cinder')
        self.send_request_if_needed.assert_called_with('cephreq')

    @patch.object(hooks, 'CephBlueStoreCompressionContext')
    @patch('charmhelpers.contrib.storage.linux.ceph.CephBrokerRq'
           '.add_op_request_access_to_group')
    @patch('charmhelpers.contrib.storage.linux.ceph.CephBrokerRq'
           '.add_op_create_replicated_pool')
    def test_create_pool_op(self, mock_create_pool,
                            mock_request_access, mock_bluestore_compression):
        self.service_name.return_value = 'cinder'
        self.test_config.set('ceph-osd-replication-count', 4)
        self.test_config.set('ceph-pool-weight', 20)
        hooks.get_ceph_request()
        mock_create_pool.assert_called_with(name='cinder', replica_count=4,
                                            weight=20, group='volumes',
                                            app_name='rbd',
                                            rbd_mirroring_mode='pool')
        mock_request_access.assert_not_called()

        self.test_config.set('restrict-ceph-pools', True)
        hooks.get_ceph_request()
        mock_create_pool.assert_called_with(name='cinder', replica_count=4,
                                            weight=20, group='volumes',
                                            app_name='rbd',
                                            rbd_mirroring_mode='pool')
        mock_request_access.assert_has_calls([
            call(
                name='volumes',
                object_prefix_permissions={'class-read': ['rbd_children']},
                permission='rwx'),
            call(
                name='images',
                object_prefix_permissions={'class-read': ['rbd_children']},
                permission='rwx'),
            call(
                name='vms',
                object_prefix_permissions={'class-read': ['rbd_children']},
                permission='rwx'),
        ])

    @patch.object(hooks, 'CephBlueStoreCompressionContext')
    @patch('charmhelpers.contrib.storage.linux.ceph.CephBrokerRq'
           '.add_op_request_access_to_group')
    @patch('charmhelpers.contrib.storage.linux.ceph.CephBrokerRq'
           '.add_op_create_replicated_pool')
    def test_create_pool_wth_name_op(self, mock_create_pool,
                                     mock_request_access,
                                     mock_bluestore_compression):
        self.service_name.return_value = 'cinder'
        self.test_config.set('ceph-osd-replication-count', 4)
        self.test_config.set('ceph-pool-weight', 20)
        self.test_config.set('rbd-pool-name', 'cinder-test')
        hooks.get_ceph_request()
        mock_create_pool.assert_called_with(name='cinder-test',
                                            replica_count=4,
                                            weight=20,
                                            group='volumes',
                                            app_name='rbd',
                                            rbd_mirroring_mode='pool')
        # confirm operation with bluestore compression
        mock_create_pool.reset_mock()
        mock_bluestore_compression().get_kwargs.return_value = {
            'compression_mode': 'fake',
        }
        hooks.get_ceph_request()
        mock_create_pool.assert_called_once_with(name='cinder-test',
                                                 replica_count=4,
                                                 weight=20,
                                                 group='volumes',
                                                 app_name='rbd',
                                                 rbd_mirroring_mode='pool',
                                                 compression_mode='fake')

    @patch.object(hooks, 'CephBlueStoreCompressionContext')
    @patch('charmhelpers.contrib.storage.linux.ceph.CephBrokerRq'
           '.add_op_create_erasure_pool')
    @patch('charmhelpers.contrib.storage.linux.ceph.CephBrokerRq'
           '.add_op_create_erasure_profile')
    @patch('charmhelpers.contrib.storage.linux.ceph.CephBrokerRq'
           '.add_op_request_access_to_group')
    @patch('charmhelpers.contrib.storage.linux.ceph.CephBrokerRq'
           '.add_op_create_pool')
    def test_create_pool_erasure_coded(self, mock_create_pool,
                                       mock_request_access,
                                       mock_create_erasure_profile,
                                       mock_create_erasure_pool,
                                       mock_bluestore_compression):
        self.service_name.return_value = 'cinder'
        self.test_config.set('ceph-osd-replication-count', 4)
        self.test_config.set('ceph-pool-weight', 20)
        self.test_config.set('pool-type', 'erasure-coded')
        self.test_config.set('ec-profile-plugin', 'isa')
        hooks.get_ceph_request()
        mock_create_pool.assert_called_with(
            name='cinder-metadata',
            replica_count=4,
            weight=0.2,
            group='volumes',
            app_name='rbd'
        )
        mock_create_erasure_pool.assert_called_with(
            name='cinder',
            erasure_profile='cinder-profile',
            weight=19.8,
            group='volumes',
            app_name='rbd',
            allow_ec_overwrites=True
        )
        mock_create_erasure_profile.assert_called_with(
            name='cinder-profile',
            k=1, m=2,
            lrc_locality=None,
            lrc_crush_locality=None,
            shec_durability_estimator=None,
            clay_helper_chunks=None,
            clay_scalar_mds=None,
            device_class=None,
            erasure_type='isa',
            erasure_technique=None
        )
        # confirm operation with bluestore compression
        mock_create_erasure_pool.reset_mock()
        mock_bluestore_compression().get_kwargs.return_value = {
            'compression_mode': 'fake',
        }
        hooks.get_ceph_request()
        mock_create_erasure_pool.assert_called_with(
            name='cinder',
            erasure_profile='cinder-profile',
            weight=19.8,
            group='volumes',
            app_name='rbd',
            allow_ec_overwrites=True,
            compression_mode='fake',
        )

    @patch('charmhelpers.core.hookenv.config')
    def test_ceph_changed_no_keys(self, mock_config):
        '''It ensures ceph assets created on ceph changed'''
        self.CONFIGS.complete_contexts.return_value = ['ceph']
        self.service_name.return_value = 'cinder'
        self.is_request_complete.return_value = True
        self.ensure_ceph_keyring.return_value = False
        hooks.hooks.execute(['hooks/ceph-relation-changed'])
        # NOTE(jamespage): If ensure_ceph keyring fails, then
        # the hook should just exit 0 and return.
        self.assertTrue(self.log.called)
        self.assertFalse(self.CONFIGS.write_all.called)

    @patch.object(hooks, 'get_ceph_request')
    @patch('charmhelpers.core.hookenv.config')
    def test_ceph_broken(self, mock_config, mock_get_ceph_request):
        self.CONFIGS.complete_contexts.return_value = ['ceph']
        self.service_name.return_value = 'cinder-ceph'
        with patch.object(hooks, 'CEPH_CONF', new="/some/random/file"):
            hooks.hooks.execute(['hooks/ceph-relation-changed'])
            hooks.hooks.execute(['hooks/ceph-relation-broken'])
        self.delete_keyring.assert_called_with(service='cinder-ceph')
        self.assertTrue(self.CONFIGS.write_all.called)
        self.remove_alternative.assert_called_with(
            os.path.basename("/some/random/file"),
            self.ceph_config_file())

    @patch('charmhelpers.core.hookenv.config')
    @patch.object(hooks, 'storage_backend')
    def test_upgrade_charm_related(self, _storage_backend, mock_config):
        self.CONFIGS.complete_contexts.return_value = ['ceph']
        self.relation_ids.return_value = ['ceph:1']
        hooks.hooks.execute(['hooks/upgrade-charm'])
        _storage_backend.assert_called_with('ceph:1')
        assert self.CONFIGS.write_all.called
        self.scrub_old_style_ceph.assert_called_once_with()

    @patch('charmhelpers.core.hookenv.config')
    @patch.object(hooks, 'storage_backend')
    def test_storage_backend_changed(self, _storage_backend, mock_config):
        hooks.hooks.execute(['hooks/storage-backend-relation-changed'])
        _storage_backend.assert_called_with()

    @patch('charmhelpers.core.hookenv.config')
    def test_storage_backend_joined_no_ceph(self, mock_config):
        self.CONFIGS.complete_contexts.return_value = []
        hooks.hooks.execute(['hooks/storage-backend-relation-joined'])
        assert self.log.called
        assert not self.relation_set.called

    @patch('charmhelpers.core.hookenv.config')
    def test_storage_backend_joined_ceph(self, mock_config):
        def func():
            return {'test': 1}
        self.CONFIGS.complete_contexts.return_value = ['ceph']
        self.service_name.return_value = 'test'
        self.CephSubordinateContext.return_value = func
        hooks.hooks.execute(['hooks/storage-backend-relation-joined'])
        self.relation_set.assert_called_with(
            relation_id=None,
            backend_name='test',
            subordinate_configuration=json.dumps({'test': 1}),
            stateless=True,
        )

    @patch('charmhelpers.core.hookenv.config')
    def test_storage_backend_replication_device(self, mock_config):
        self.application_name.return_value = 'test'
        app_name = '{}-replication-device'.format(self.application_name())
        self.service_name.return_value = app_name
        self.CONFIGS.complete_contexts.return_value = [
            'ceph', 'ceph-replication-device']

        def func():
            return {
                'cinder': {
                    '/etc/cinder/cinder.conf': {
                        'sections': {
                            'test': []
                        }
                    }
                }
            }

        self.CephSubordinateContext.return_value = func
        hooks.hooks.execute(['hooks/storage-backend-relation-joined'])

        replication_device = {
            'backend_id': 'ceph',
            'conf': self.ceph_replication_device_config_file(),
            'user': 'test-replication-device'
        }
        replication_device_str = ','.join(
            ['{}:{}'.format(k, v) for k, v in replication_device.items()])
        expected_config = {
            'cinder': {
                '/etc/cinder/cinder.conf': {
                    'sections': {
                        'test': [
                            ('replication_device', replication_device_str)
                        ]
                    }
                }
            }
        }
        self.relation_set.assert_called_with(
            relation_id=None,
            backend_name='test-replication-device',
            subordinate_configuration=json.dumps(expected_config),
            stateless=True,
        )

    @patch.object(hooks, 'ceph_access_joined')
    @patch.object(hooks, 'storage_backend')
    def test_leader_settings_changed(self,
                                     storage_backend,
                                     ceph_access_joined):
        self.relation_ids.side_effect = [['ceph-access:1'],
                                         ['storage-backend:23']]
        hooks.leader_settings_changed()
        ceph_access_joined.assert_called_with('ceph-access:1')
        storage_backend.assert_called_with('storage-backend:23')

    @patch.object(hooks, 'CONFIGS')
    def test_ceph_access_joined_no_ceph(self,
                                        CONFIGS):
        CONFIGS.complete_contexts.return_value = []
        hooks.ceph_access_joined()
        self.relation_set.assert_not_called()

    @patch.object(hooks, 'CONFIGS')
    def test_ceph_access_joined_follower_unseeded(self,
                                                  CONFIGS):
        CONFIGS.complete_contexts.return_value = ['ceph']
        self.is_leader.return_value = False
        self.leader_get.return_value = None
        hooks.ceph_access_joined()
        self.relation_set.assert_not_called()

    @patch.object(hooks, 'CephContext')
    @patch.object(hooks, 'CONFIGS')
    def test_ceph_access_joined_leader(self,
                                       CONFIGS,
                                       CephContext):
        CONFIGS.complete_contexts.return_value = ['ceph']
        self.is_leader.return_value = True
        self.leader_get.side_effect = [None, 'newuuid']
        context = MagicMock()
        context.return_value = {'key': 'mykey'}
        CephContext.return_value = context
        hooks.ceph_access_joined()
        self.leader_get.assert_called_with('secret-uuid')
        self.leader_set.assert_called_with({'secret-uuid': ANY})
        self.relation_set.assert_called_with(
            relation_id=None,
            relation_settings={'key': 'mykey',
                               'secret-uuid': 'newuuid'}
        )

    @patch.object(hooks, 'CephContext')
    @patch.object(hooks, 'CONFIGS')
    def test_ceph_access_joined_follower_seeded(self,
                                                CONFIGS,
                                                CephContext):
        CONFIGS.complete_contexts.return_value = ['ceph']
        self.is_leader.return_value = False
        self.leader_get.return_value = 'newuuid'
        context = MagicMock()
        context.return_value = {'key': 'mykey'}
        CephContext.return_value = context
        hooks.ceph_access_joined()
        self.leader_get.assert_called_with('secret-uuid')
        self.leader_set.assert_not_called()
        self.relation_set.assert_called_with(
            relation_id=None,
            relation_settings={'key': 'mykey',
                               'secret-uuid': 'newuuid'}
        )

    @patch.object(hooks, 'ceph_changed')
    @patch.object(hooks.uuid, 'uuid4')
    def test_write_and_restart(self, mock_uuid4, mock_ceph_changed):
        # confirm normal operation for any unit type
        mock_ceph_changed.side_effect = None
        hooks.write_and_restart()
        self.CONFIGS.write_all.assert_called_once_with()
        # confirm normal operation for leader
        self.leader_get.reset_mock()
        self.leader_get.return_value = None
        self.is_leader.return_value = True
        mock_uuid4.return_value = 42
        hooks.write_and_restart()
        self.leader_get.assert_called_once_with('secret-uuid')
        self.leader_set.assert_called_once_with({'secret-uuid': '42'})

    @patch.object(hooks, 'get_ceph_request')
    @patch.object(hooks, 'is_request_complete')
    @patch.object(hooks, 'CephBlueStoreCompressionContext')
    @patch.object(hooks, 'set_os_workload_status')
    def test_assess_status(self,
                           mock_set_os_workload_status,
                           mock_bluestore_compression,
                           is_request_complete,
                           get_ceph_request):
        is_request_complete.return_value = True
        hooks.assess_status()
        self.os_application_version_set.assert_called_once_with(
            hooks.VERSION_PACKAGE)
        mock_set_os_workload_status.assert_called_once_with(
            ANY, hooks.REQUIRED_INTERFACES)
        mock_bluestore_compression().validate.assert_called_once_with()
        self.assertFalse(self.status_set.called)
        # confirm incomplete request is caught
        self.status_set.reset_mock()
        is_request_complete.return_value = False
        hooks.assess_status()
        self.status_set.assert_called_once_with(
            'waiting', 'Ceph broker request incomplete')
        # confirm operation when user have provided invalid configuration
        is_request_complete.return_value = True
        self.status_set.reset_mock()
        mock_bluestore_compression().validate.side_effect = ValueError(
            'fake message')
        hooks.assess_status()
        self.status_set.assert_called_once_with(
            'blocked', 'Invalid configuration: fake message')

    @patch('charmhelpers.core.hookenv.config')
    @patch.object(hooks, 'storage_backend')
    def test_ceph_replication_device_changed(self,
                                             storage_backend,
                                             mock_config):
        self.CONFIGS.complete_contexts.return_value = [
            'ceph-replication-device']
        self.ensure_ceph_keyring.return_value = True
        self.relation_ids.return_value = ['storage-backend:1']
        app_name = '{}-replication-device'.format(self.application_name())
        hooks.hooks.execute(['hooks/ceph-replication-device-relation-changed'])
        self.ensure_ceph_keyring.assert_called_with(
            service=app_name,
            relation='ceph-replication-device',
            user='cinder',
            group='cinder')
        self.assertTrue(self.CONFIGS.write_all.called)
        storage_backend.assert_called_with('storage-backend:1')

    @patch('charmhelpers.core.hookenv.config')
    def test_ceph_replication_device_broken(self, mock_config):
        app_name = '{}-replication-device'.format(self.application_name())
        self.service_name.return_value = app_name
        hooks.hooks.execute(['hooks/ceph-replication-device-relation-broken'])
        self.delete_keyring.assert_called_with(
            service=self.service_name.return_value)
        self.assertTrue(self.CONFIGS.write_all.called)

    @patch('charmhelpers.core.hookenv.config')
    def test_ceph_replication_device_joined(self, mock_config):
        data = {'application-name': '{}-replication-device'.format(
            self.application_name())}
        hooks.hooks.execute(['hooks/ceph-replication-device-relation-joined'])
        self.relation_set.assert_called_with(relation_settings=data)
