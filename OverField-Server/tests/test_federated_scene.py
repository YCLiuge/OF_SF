"""Authority and recovery invariants for the public scene shard."""
import unittest
from types import SimpleNamespace

from federated_scene import LEASE, SceneOwner


class SceneTest(unittest.TestCase):
    def setUp(self):
        self.time = 10.0
        self.world = SimpleNamespace(node_id="b", owners={10:"b", 11:"c"},
            uid_ranges={"a":(1000001,1999999), "b":(2000001,2999999), "c":(3000001,3999999)})
        self.owner = SceneOwner(self.world, (1, 100), lambda:self.time)

    def actor(self, uid, channel=1, scene=10):
        return [uid, scene, channel, "旅人", 101001, 15, 1, [11, 0, 20, 2], [0, 0, 0, 0]]

    def publish(self, node, rows, seq=1, epoch=1):
        return self.owner.exchange(node, {"op":"scene.publish","epoch":epoch,"sequence":seq,"actors":rows})

    def read(self, reader, source):
        return self.owner.exchange(reader, {"op":"scene.read","source":source})["actors"]

    def test_room_subscription_and_departure(self):
        a = self.actor(1000001)
        other = self.actor(1000002, channel=3)
        self.publish("a", [a, other])
        self.assertEqual(self.read("b","a"), [])
        self.publish("b", [self.actor(2000001)])
        self.assertEqual(self.read("b","a"), [a])
        self.publish("a", [other], seq=2)
        self.assertEqual(self.read("b","a"), [])

    def test_lease_fences_retries_and_generation(self):
        a, b = self.actor(1000001), self.actor(2000001)
        self.publish("a", [a], seq=7)
        first=self.publish("b", [b])
        renewed=self.owner.exchange("b", {"op":"scene.renew","boot":first["boot"],"sequence":2,"epoch":1})
        self.assertEqual(renewed["sources"],first["sources"])
        self.time += LEASE + 1
        self.assertEqual(self.read("b","a"), [])
        with self.assertRaises(ValueError):
            self.publish("a", [a], seq=7)
        with self.assertRaises(ValueError):
            self.owner.exchange("b", {"op":"scene.renew","boot":first["boot"],"sequence":3,"epoch":1})
        self.publish("a", [a], epoch=2)
        with self.assertRaises(ValueError):
            self.publish("a", [], seq=999, epoch=1)
        self.publish("b", [b], seq=3)
        self.assertEqual(self.read("b","a"), [a])

    def test_wrong_owner_range_and_private_channels(self):
        a = self.actor(1000001)
        self.publish("a", [a])
        self.publish("b", [self.actor(2000001)])
        for invalid in ([self.actor(2000010)], [self.actor(1000001, channel=1000002)],
                        [self.actor(1000001, scene=11)], [a, a], [a]*513, [a[:5]]):
            with self.assertRaises(ValueError):
                self.publish("a", invalid, seq=2)
        self.assertEqual(self.read("b","a"), [a])


if __name__ == "__main__":
    unittest.main()
