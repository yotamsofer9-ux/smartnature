import io
import unittest
from PIL import Image
from domain import (eligible_reserves, session_identity, validate_hazard,
                    validate_recommendations, validate_image, unknown_hazard)


class DomainTests(unittest.TestCase):
    def test_private_car_never_receives_offroad_closed_or_unknown(self):
        rows=[dict(id=1,status="Green",accessibility="4x4 Only"),
              dict(id=2,status="Closed",accessibility="Accessible"),
              dict(id=3,status="Unknown",accessibility="Accessible"),
              dict(id=4,status="Yellow",accessibility="Accessible")]
        self.assertEqual([r['id'] for r in eligible_reserves(rows,"Private Car")],[4])
        self.assertEqual(eligible_reserves(rows,"unrecognized"),[])

    def test_untrusted_model_cannot_select_a_filtered_out_site(self):
        with self.assertRaises(ValueError):
            validate_recommendations({'recommendations':[{'id':9,'reason':'test'}]},[{'id':1}])
        with self.assertRaises(ValueError):
            validate_recommendations({'recommendations':[{'id':True,'reason':'test'}]},[{'id':1}])

    def test_model_error_is_unknown_not_a_danger_or_clearance(self):
        from ai_service import AIService
        service=AIService.__new__(AIService)
        def broken(*args): raise RuntimeError('upstream failure')
        service._generate=broken
        result,ok=service.hazard('test')
        self.assertFalse(ok)
        self.assertEqual(result['suggested_status'],'Unknown')
        self.assertEqual(result,unknown_hazard())

    def test_hazard_rejects_invalid_conflicting_or_nonobject_json(self):
        for value in ['[]', {'severity':'Red','suggested_status':'Green','short_description':'test'},
                      {'severity':'Critical','suggested_status':'Critical','short_description':'test'}]:
            with self.assertRaises(ValueError): validate_hazard(value)

    def test_private_car_request_for_recognized_offroad_site_is_explained(self):
        from domain import requested_exclusions
        rows=[dict(id=1,name='מסלול מכתש רמון',status='Green',accessibility='4x4 Only')]
        self.assertEqual(len(requested_exclusions('אני רוצה מכתש רמון',rows,'Private Car')),1)
        self.assertEqual(requested_exclusions('אני רוצה מכתש רמון',rows,'4x4'),[])

    def test_demo_review_never_reopens_closed_site(self):
        from domain import apply_demo_reviews
        rows=[dict(id=1,status='Closed',description='historical closure')]
        reports=[dict(reserve_id=1,review_status='demo_approved',analysis={
            'suggested_status':'Green','short_description':'unverified'})]
        self.assertEqual(apply_demo_reviews(rows,reports)[0]['status'],'Closed')

    def test_cloud_runtime_never_loads_local_keys(self):
        from unittest.mock import patch
        from auth_manager import get_credentials
        with patch.dict('os.environ',{'K_SERVICE':'smartnature'}), \
             patch('auth_manager._secrets',side_effect=AssertionError('key read')), \
             patch('auth_manager.google.auth.default',return_value=('managed-identity','project')):
            self.assertEqual(get_credentials(),('managed-identity','adc'))

    def test_session_resumes_but_another_visitor_is_isolated(self):
        token,sid=session_identity()
        self.assertEqual(session_identity(token),(token,sid))
        self.assertNotEqual(session_identity()[1],sid)
        self.assertNotEqual(token,sid)
        self.assertNotEqual(session_identity('malformed')[0],'malformed')

    def test_upload_is_reencoded_and_metadata_removed(self):
        image=Image.new('RGB',(20,20),'green')
        exif=Image.Exif();exif[270]='private metadata'
        stream=io.BytesIO();image.save(stream,format='JPEG',exif=exif)
        data,mime=validate_image(stream.getvalue())
        self.assertEqual(mime,'image/jpeg')
        self.assertFalse(Image.open(io.BytesIO(data)).getexif())
        for bad in [b'not an image', b'x'*(5*1024*1024+1)]:
            with self.assertRaises(ValueError): validate_image(bad)


if __name__=='__main__': unittest.main()
