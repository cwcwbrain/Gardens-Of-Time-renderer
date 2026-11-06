"""GAF compression signatures exercised with a real, complete atlas-v3 payload."""
import struct
import unittest
import zlib
from pathlib import Path

from gardens.gaf import GAFError,decode


FIXTURE=Path(__file__).parent/'fixtures/YucatanCenote.gaf'


class GAFSignatureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cag=FIXTURE.read_bytes()
        if cls.cag[:4]!=b'CAG\0':raise AssertionError('Expected the real compressed Yucatan fixture')
        cls.version=cls.cag[4:6]
        cls.raw=zlib.decompress(cls.cag[10:])
        cls.movie=decode(cls.cag)

    def wrap(self,signature,body=None,length=None,version=None):
        body=self.raw if body is None else body
        length=len(body) if length is None else length
        version=self.version if version is None else bytes(version)
        return signature+version+struct.pack('<I',length)+body

    def test_official_fag_and_cag_decode_the_same_complete_movie(self):
        self.assertEqual(self.movie.version,(5,9))
        self.assertEqual(len(next(a for a in self.movie.atlases if a['timeline']==0)['elements']),76)
        self.assertGreater(len(self.movie.timelines),1)
        for declared in (len(self.raw),len(self.raw)+10):
            for signature,body in ((b'FAG\0',self.raw),(b'CAG\0',self.cag[10:])):
                with self.subTest(signature=signature,declared=declared):
                    self.assertEqual(decode(self.wrap(signature,body,declared)),self.movie)

    def test_previously_supported_gaf_spelling_is_a_validated_raw_compatibility_alias(self):
        for declared in (len(self.raw),len(self.raw)+10):
            with self.subTest(declared=declared):
                self.assertEqual(decode(self.wrap(b'GAF\0',length=declared)),self.movie)
        with self.assertRaises(GAFError):decode(self.wrap(b'GAF\0',self.raw+b'junk'))

    def test_fag_does_not_guess_or_inflate_a_compressed_body(self):
        compressed=self.cag[10:]
        # Adjusting the declared length must not turn wrong compression into a
        # fallback decoding path; the bounded raw parser still has to reject it.
        for declared in (len(self.raw),len(compressed),len(compressed)+10):
            with self.subTest(declared=declared),self.assertRaises(GAFError):
                decode(self.wrap(b'FAG\0',compressed,declared))

    def test_cag_does_not_fall_back_to_a_raw_body(self):
        for declared in (len(self.raw),len(self.raw)+10):
            with self.subTest(declared=declared),self.assertRaises(GAFError):
                decode(self.wrap(b'CAG\0',self.raw,declared))

    def test_unknown_magic_and_unsupported_major_versions_fail_closed(self):
        for signature in (b'ZIP\0',b'fag\0',b'FAG1',b'FAG\1',b'\0GAF'):
            with self.subTest(signature=signature),self.assertRaises(GAFError):
                decode(self.wrap(signature))
        for signature in (b'FAG\0',b'CAG\0'):
            body=self.raw if signature==b'FAG\0' else self.cag[10:]
            for major in (0,3,6,255):
                with self.subTest(signature=signature,major=major),self.assertRaises(GAFError):
                    decode(self.wrap(signature,body,len(self.raw),version=(major,9)))

    def test_short_headers_are_bounded(self):
        for data in (self.cag,self.wrap(b'FAG\0')):
            for length in (0,1,3,4,5,6,7,9):
                with self.subTest(signature=data[:4],length=length),self.assertRaises(GAFError):
                    decode(data[:length])

    def test_truncated_raw_payload_fails_with_original_or_adjusted_length(self):
        for removed in (1,6,len(self.raw)-4,len(self.raw)):
            truncated=self.raw[:-removed]
            for declared in (len(self.raw),len(truncated),len(truncated)+10):
                with self.subTest(removed=removed,declared=declared),self.assertRaises(GAFError):
                    decode(self.wrap(b'FAG\0',truncated,declared))

    def test_trailing_raw_junk_is_rejected_even_with_adjusted_length(self):
        for extra in (b'x',b'junk',b'\0'*6):
            body=self.raw+extra
            for declared in (len(body),len(body)+10):
                with self.subTest(extra=extra,declared=declared),self.assertRaises(GAFError):
                    decode(self.wrap(b'FAG\0',body,declared))

    def test_zlib_truncation_trailing_data_and_concatenated_streams_fail_closed(self):
        bodies=[self.cag[10:-1],self.cag[10:-4],self.cag[10:]+b'junk',
                self.cag[10:]+zlib.compress(self.raw)]
        for number,body in enumerate(bodies):
            with self.subTest(case=number),self.assertRaises(GAFError):
                decode(self.wrap(b'CAG\0',body,len(self.raw)))


if __name__=='__main__':unittest.main()
