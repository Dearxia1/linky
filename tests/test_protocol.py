import unittest
from linky import serialize_key, reconstruct_key
from pynput.keyboard import Key, KeyCode

class TestProtocol(unittest.TestCase):
    def test_serialize_char_key(self):
        key = KeyCode.from_char('a')
        serialized = serialize_key(key)
        self.assertEqual(serialized, {"char": "a"})
        
        reconstructed = reconstruct_key(serialized)
        self.assertEqual(reconstructed.char, "a")

    def test_serialize_special_key(self):
        key = Key.ctrl
        serialized = serialize_key(key)
        self.assertEqual(serialized, {"name": "ctrl"})
        
        reconstructed = reconstruct_key(serialized)
        self.assertEqual(reconstructed, Key.ctrl)

    def test_serialize_vk_key(self):
        # Virtual key code for 'a' on Windows is usually 65
        key = KeyCode.from_vk(65)
        serialized = serialize_key(key)
        self.assertEqual(serialized, {"vk": 65})
        
        reconstructed = reconstruct_key(serialized)
        self.assertEqual(reconstructed.vk, 65)

if __name__ == '__main__':
    unittest.main()
