from datetime import UTC, datetime, timedelta
import unittest
from brain.vision.contracts import CAMERA_FRAME_V1, BoundingBox, CameraFrame, Detection, DetectionResult
from brain.vision.events import DetectionEvent, VideoArtifactRef, canonical_from_detection_result

class VisionEventTests(unittest.TestCase):
 def test_adapter_preserves_source_and_makes_only_real_tracks(self):
  now=datetime(2026,7,23,tzinfo=UTC); frame=CameraFrame(CAMERA_FRAME_V1,"d","c","s",1,now,now,"cal","a"*64,"jpeg",10,10,0,0)
  result=DetectionResult("detection_result.v1",frame,"m","v",now,(Detection("person",.9,BoundingBox(1,1,2,2),"t"),Detection("car",.8,BoundingBox(4,4,2,2))))
  summary=canonical_from_detection_result(result,ttl=timedelta(seconds=1))
  self.assertEqual(len(summary.events),2); self.assertEqual(len(summary.tracks),1); self.assertEqual(summary.events[0].source_frame,frame)
  self.assertEqual(summary.state(now+timedelta(seconds=2)).value,"stale")

 def test_untracked_detection_never_creates_a_tracked_object(self):
  now=datetime(2026,7,23,tzinfo=UTC); frame=CameraFrame(CAMERA_FRAME_V1,"d","c","s",1,now,now,"cal","a"*64,"jpeg",10,10,0,0)
  result=DetectionResult("detection_result.v1",frame,"m","v",now,(Detection("person",.9,BoundingBox(1,1,2,2)),))
  summary=canonical_from_detection_result(result,ttl=timedelta(seconds=1))
  self.assertEqual(len(summary.events),1); self.assertEqual(summary.tracks,())

 def test_event_rejects_artifact_from_another_frame(self):
  now=datetime(2026,7,23,tzinfo=UTC); frame=CameraFrame(CAMERA_FRAME_V1,"d","c","s",1,now,now,"cal","a"*64,"jpeg",10,10,0,0); other=CameraFrame(CAMERA_FRAME_V1,"d","c","s",2,now,now,"cal","b"*64,"jpeg",10,10,0,0)
  artifact=VideoArtifactRef("video_artifact_ref.v1","clip",other,"jpeg","b"*64,now,timedelta(seconds=1))
  with self.assertRaises(ValueError): DetectionEvent("detection_event.v1","event",frame,"m","v","person",.9,BoundingBox(1,1,2,2),now,timedelta(seconds=1),artifact)
