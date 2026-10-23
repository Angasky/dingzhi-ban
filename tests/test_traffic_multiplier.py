import unittest
from unittest.mock import patch

import server
import test_subscription_enforcement as fixture


class MultiplierTests(unittest.TestCase):
    setUp=fixture.EnforcementTests.setUp
    configure=fixture.EnforcementTests.configure

    def test_twice_raw_usage_is_billed_and_enforced(self):
        record=self.configure(total=500);record['traffic']['multiplierUnits']=2000
        self.manager.inbounds.return_value['inbounds'][0].update(up=100,down=150)
        server.enforce_profile('clash')
        info=server.traffic_info(record)
        self.assertEqual(info['rawUsed'],250);self.assertEqual(info['used'],500)
        self.assertEqual(info['remaining'],0);self.assertEqual(info['multiplier'],2)
        self.assertIn('upload=200; download=300',server.subscription_userinfo(record))
        self.manager.delete_inbound.assert_called_once_with('server-a',1)

    def test_fractional_bytes_survive_samples_reset_and_restart(self):
        record=self.configure(total=1000);record['traffic']['multiplierUnits']=500
        row=self.manager.inbounds.return_value['inbounds'][0]
        for value in (1,2,1):
            row['up']=value;server.enforce_profile('clash')
        self.assertEqual(server.traffic_info(record)['used'],1)
        server.PROFILES_CACHE=None
        row['up']=2;server.enforce_profile('clash')
        info=server.traffic_info(server.profile_record())
        self.assertEqual(info['used'],2);self.assertEqual(info['rawUsed'],4)

    def test_rate_change_settles_old_rate_without_overwriting_new_sample(self):
        record=self.configure(total=1000)
        row=self.manager.inbounds.return_value['inbounds'][0];row.update(up=3,down=0)
        server.update_traffic({'enforce':True,'upload':0,'download':0,'total':1000,'multiplier':2,'preserveUsage':True})
        self.assertEqual(server.traffic_info(record)['used'],3)
        row['up']=5;server.enforce_profile('clash')
        self.assertEqual(server.traffic_info(record)['used'],7)
        self.assertEqual(server.traffic_info(record)['rawUsed'],5)

    def test_offline_rate_change_is_rejected_and_old_rate_remains(self):
        record=self.configure(total=1000)
        self.manager.inbounds.side_effect=ValueError('offline')
        with self.assertRaisesRegex(ValueError,'结算失败'):
            server.update_traffic({'multiplier':2,'preserveUsage':True})
        self.assertEqual(record['traffic'].get('multiplierUnits',1000),1000)
        self.manager.delete_inbound.assert_not_called()

    def test_zero_rate_keeps_raw_usage_and_still_expires(self):
        record=self.configure(total=1);record['traffic']['multiplierUnits']=0
        self.manager.inbounds.return_value['inbounds'][0].update(up=100000,down=100000)
        server.enforce_profile('clash')
        self.assertEqual(server.traffic_info(record)['used'],0)
        self.assertEqual(server.traffic_info(record)['rawUsed'],200000)
        self.manager.delete_inbound.assert_not_called()
        record['traffic']['expire']=1;server.enforce_profile('clash')
        self.manager.delete_inbound.assert_called_once()

    def test_invalid_multiplier_never_contacts_servers(self):
        self.configure()
        for value in (True,-1,101,'nan','Infinity',0.0001,{},None):
            with self.subTest(value=value),self.assertRaises(ValueError):server.update_traffic({'multiplier':value})
        self.manager.inbounds.assert_not_called()
        self.manager.ensure_meter.assert_not_called()
