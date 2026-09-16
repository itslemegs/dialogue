"""Create-time cover uploads with temporary files and fake DB sessions only."""
import ast
from datetime import datetime, timedelta, timezone
from io import BytesIO
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

from fastapi import Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import RedirectResponse
from starlette.datastructures import Headers
from PIL import Image
from app import i18n
from app.models import Event, EventStage, EventAccessMode
from app.security import role_required
from app.services import event_covers as covers

ROOT = Path(__file__).resolve().parents[1]


def load_function(name, namespace):
    node=next(n for n in ast.parse((ROOT/'app/main.py').read_text()).body
              if isinstance(n,ast.FunctionDef) and n.name==name)
    node.decorator_list=[]
    exec(compile(ast.fix_missing_locations(ast.Module(body=[ast.ImportFrom(module='__future__',names=[ast.alias(name='annotations')],level=0),node],type_ignores=[])),'isolated_cover_route','exec'),namespace)
    return namespace[name]


def upload(fmt='PNG', filename=None, data=None):
    extension={'PNG':'png','JPEG':'jpg','WEBP':'webp'}[fmt]
    if data is None:
        buffer=BytesIO();Image.new('RGB',(20,10),'red').save(buffer,format=fmt);data=buffer.getvalue()
    return UploadFile(filename=filename or 'cover.'+extension,file=BytesIO(data),
                      headers=Headers({'content-type':{'PNG':'image/png','JPEG':'image/jpeg','WEBP':'image/webp'}[fmt]}))


class EventCoverTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.directory=Path(self.temp.name)/'covers'
        self.patch=patch.object(covers,'COVER_DIR',self.directory);self.patch.start();self.addCleanup(self.patch.stop)
        self.db=MagicMock();self.db.__enter__.return_value=self.db
        self.events=[]
        def add(event): self.events.append(event)
        self.db.add.side_effect=add
        self.db.flush.side_effect=lambda:setattr(self.events[-1],'id',17)
        self.ns=dict(Request=Request,Depends=Depends,Form=Form,File=File,UploadFile=UploadFile,
            role_required=role_required,datetime=datetime,timedelta=timedelta,timezone=timezone,JST=ZoneInfo('Asia/Tokyo'),
            SimpleNamespace=SimpleNamespace,Event=Event,EventStage=EventStage,EventAccessMode=EventAccessMode,
            get_session=lambda:self.db,hash_password=lambda p:'hashed',_flags=lambda u:(True,True,True),
            render=lambda name,request,**ctx:ctx,RedirectResponse=RedirectResponse,
            translate=i18n.translate,request_locale=lambda r:'en',
            CoverValidationError=covers.CoverValidationError,validate_cover=covers.validate_cover,
            save_cover=covers.save_cover,delete_cover=covers.delete_cover)
        self.create=load_function('admin_create_event',self.ns)
        self.args=dict(request=object(),user=SimpleNamespace(id=1),title='Event',start_local='2027-01-01T10:00',
                       open_min=10,debate_min=20,vote_min=10,access_mode='open',passcode='',cover_image=None)

    def test_create_without_cover_and_existing_null_fallback(self):
        response=self.create(**self.args)
        self.assertEqual(response.status_code,303)
        self.assertIsNone(self.events[0].cover_image)
        self.assertEqual(covers.cover_image_url(self.events[0].cover_image),covers.DEFAULT_COVER)
        self.assertFalse(self.directory.exists())
        self.db.commit.assert_called_once()

    def test_create_all_valid_formats_unique_normalized_paths(self):
        paths=[]
        for fmt in ('JPEG','PNG','WEBP'):
            response=self.create(**{**self.args,'cover_image':upload(fmt)})
            self.assertEqual(response.status_code,303)
            path=self.events[-1].cover_image;paths.append(path)
            self.assertTrue(covers.NAME.fullmatch(path.removeprefix(covers.URL_PREFIX)))
            with Image.open(self.directory/path.split('/')[-1]) as image:
                self.assertEqual(image.format,'WEBP');self.assertEqual(image.size,(20,10))
        self.assertEqual(len(set(paths)),3)

    def test_invalid_type_content_and_size_never_create_event(self):
        for image in (upload(filename='attack.svg'),upload(filename='attack.gif'),
                      upload(data=b'not an image'),upload(data=b'x'*(covers.MAX_BYTES+1))):
            result=self.create(**{**self.args,'cover_image':image})
            self.assertTrue(result['error'])
        self.db.add.assert_not_called();self.db.commit.assert_not_called()
        self.assertFalse(self.directory.exists())
        mismatch=upload();mismatch.headers=Headers({'content-type':'image/jpeg'})
        with self.assertRaises(covers.CoverValidationError):covers.validate_cover(mismatch)

    def test_regular_validation_precedes_image_read(self):
        image=upload();image.file=MagicMock()
        result=self.create(**{**self.args,'open_min':0,'cover_image':image})
        self.assertTrue(result['error']);image.file.read.assert_not_called()
        self.db.add.assert_not_called()

    def test_commit_failure_cleans_cover_but_refresh_failure_keeps_it(self):
        self.db.commit.side_effect=RuntimeError('commit failed')
        with self.assertRaises(RuntimeError):self.create(**{**self.args,'cover_image':upload()})
        self.assertEqual(list(self.directory.iterdir()),[])
        self.db.commit.side_effect=None;self.db.refresh.side_effect=RuntimeError('refresh failed')
        with self.assertRaises(RuntimeError):self.create(**{**self.args,'cover_image':upload()})
        self.assertEqual(len(list(self.directory.iterdir())),1)

    def test_safe_paths_deletion_missing_and_symlinks(self):
        data=covers.validate_cover(upload(filename='../../cover.png'))
        path=covers.save_cover(data)
        outside=Path(self.temp.name)/'outside';outside.write_text('keep')
        for bad in ('../../outside','/etc/passwd',covers.DEFAULT_COVER,covers.URL_PREFIX+'../outside',"https://evil.test/a.webp"):
            self.assertEqual(covers.cover_image_url(bad),covers.DEFAULT_COVER)
            covers.delete_cover(bad)
        owned=self.directory/path.split('/')[-1];owned.unlink();owned.symlink_to(outside)
        covers.delete_cover(path);self.assertEqual(outside.read_text(),'keep')
        owned.unlink();covers.delete_cover(path)  # Missing is safe.
        path=covers.save_cover(data);covers.delete_cover(path)
        self.assertEqual(list(self.directory.iterdir()),[])

    def test_delete_cleanup_only_after_commit(self):
        path=covers.save_cover(covers.validate_cover(upload()))
        self.db.get.return_value=SimpleNamespace(cover_image=path)
        ns=dict(self.ns, Query=lambda value:value,hard_delete_event=MagicMock())
        delete=load_function('admin_delete_event',ns)
        self.db.commit.side_effect=RuntimeError('failed')
        with self.assertRaises(RuntimeError):delete(object(),17,next='/admin')
        self.assertTrue((self.directory/path.split('/')[-1]).exists())
        self.db.commit.side_effect=None
        self.assertEqual(delete(object(),17,next='/admin').status_code,303)
        self.assertFalse((self.directory/path.split('/')[-1]).exists())

    def test_dashboard_uses_safe_url_and_records_visual_is_unchanged(self):
        event=Event(id=17,title='Test',starts_at=datetime(2027,1,1,tzinfo=timezone.utc),stages=[])
        self.db.exec.return_value.all.return_value=[event]
        ns=dict(datetime=datetime,timezone=timezone,select=MagicMock(),selectinload=MagicMock(),
                or_=lambda *args:None,Event=Event,json=json,to_iso_z=lambda dt:dt.isoformat(),cover_image_url=covers.cover_image_url)
        cards=load_function('_dashboard_event_cards',ns)
        for value in (None,covers.URL_PREFIX+'event-cover-'+'a'*32+'.webp',"');evil"):
            event.cover_image=value
            card=cards(self.db,SimpleNamespace(cookies={}),ZoneInfo('Asia/Tokyo'))[0]
            self.assertEqual(card['cover_image_url'],covers.cover_image_url(value))
        text=(ROOT/'app/templates/dashboard.html').read_text()
        self.assertIn("url('{{ ev.cover_image_url }}')",text)
        self.assertEqual(text.count('home-bg.jpg'),1)
        self.assertIn('bg-cover bg-center',text)

    def test_forms_and_creation_role_dependency(self):
        for file in ('admin_event_new.html','admin/_events.html'):
            text=(ROOT/'app/templates'/file).read_text()
            self.assertIn('enctype="multipart/form-data"',text)
            self.assertIn('name="cover_image"',text)
        node=next(n for n in ast.parse((ROOT/'app/main.py').read_text()).body if isinstance(n,ast.FunctionDef) and n.name=='admin_create_event')
        self.assertIn("role_required('president', 'chairman')",ast.unparse(node))
        dependency=role_required('president','chairman')
        import app.security as security
        flags={'IS_ADMIN':False,'IS_PRESIDENT':False,'IS_CHAIR':False,'IS_MEMBER':True,'IS_BANNED':False}
        with patch.object(security,'effective_flags',return_value=flags):
            with self.assertRaises(HTTPException) as error:dependency(user=object())
            self.assertEqual(error.exception.status_code,403)
        for role in ('IS_PRESIDENT','IS_CHAIR'):
            with patch.object(security,'effective_flags',return_value={**flags,role:True}):
                user=object();self.assertIs(dependency(user=user),user)
