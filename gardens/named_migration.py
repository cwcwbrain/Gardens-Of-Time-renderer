"""Journaled moves of unchanged, owned legacy named exports into chapter groups."""
import hashlib
import json
import os
import re
from pathlib import Path,PurePosixPath

from .download import sha256

JOURNAL='.named_layout_migration.json'
INDEX='named_scene_index.csv'
IDENTITY=('scene_id','variant_id','role','official_title','official_chapter_title',
          'chapter_number','order','source_proof_fingerprint')


def _identity(record):return {key:record.get(key) for key in IDENTITY}


def _key(identity,digest):
    value=(tuple(identity.get(key) for key in IDENTITY),digest)
    try:hash(value)
    except TypeError:return None
    return value


def _digest(value):return isinstance(value,str) and re.fullmatch(r'[0-9a-f]{64}',value) is not None


def _relative(value,parts=None):
    if not isinstance(value,str) or not value or '\\' in value:
        raise ValueError('Named migration contains an invalid relative path')
    path=PurePosixPath(value)
    if path.is_absolute() or '..' in path.parts or str(path)!=value or (parts and len(path.parts)!=parts):
        raise ValueError('Named migration path is not a safe relative export path')
    return path


class LegacyMigration:
    def __init__(self,root,previous,plain,atomic_bytes):
        self.plain,self.atomic_bytes=plain,atomic_bytes
        self.root=plain(root);self.previous=previous;self.path=plain(self.root/JOURNAL)
        self.entries={};self.cancelled=set();self.intended=None;self.journal_hash=None
        self.legacy={};self.pending={}
        self.prior=previous.get('index_sha256')
        for record in previous.get('files',[]):
            if not isinstance(record,dict):continue
            try:_relative(record.get('relative_path'),4)
            except ValueError:continue
            digest=record.get('source_sha256')
            if (not _digest(digest) or record.get('destination_sha256')!=digest or
                    previous.get('owned_files',{}).get(record['relative_path'])!=digest):continue
            key=_key(_identity(record),digest)
            if key is not None:self.legacy.setdefault(key,[]).append(record)
        for records in self.legacy.values():records.sort(key=lambda record:record['relative_path'])
        if self.path.exists():
            raw=self.path.read_bytes()
            try:journal=json.loads(raw)
            except (ValueError,UnicodeError) as exc:raise ValueError('Invalid named migration journal') from exc
            if not isinstance(journal,dict) or journal.get('schema')!=1 or not isinstance(journal.get('entries'),list):
                raise ValueError('Unrecognized named migration journal')
            self.prior=journal.get('previous_index_sha256');self.intended=journal.get('intended_index_sha256')
            if any(value is not None and not _digest(value) for value in (self.prior,self.intended)):
                raise ValueError('Invalid named migration index proof')
            for entry in journal['entries']:
                self._validate(entry)
                if entry['old_relative'] in self.entries:raise ValueError('Duplicate legacy path in named migration journal')
                self.entries[entry['old_relative']]=entry
            self.journal_hash=hashlib.sha256(raw).hexdigest()
        self._index_pending()

    def _index_pending(self):
        self.pending={}
        for entry in self.entries.values():
            key=_key(entry['identity'],entry['digest'])
            if key is None:raise ValueError('Invalid pending named migration identity')
            self.pending.setdefault(key,[]).append(entry)

    def _path(self,relative):
        path=self.plain(self.root.joinpath(*_relative(relative).parts))
        if path==self.root or not path.is_relative_to(self.root):raise ValueError('Named migration escapes export folder')
        return path

    def _validate(self,entry):
        if not isinstance(entry,dict) or not _digest(entry.get('digest')) or not isinstance(entry.get('identity'),dict):
            raise ValueError('Invalid named migration entry')
        old=_relative(entry.get('old_relative'),4);new=_relative(entry.get('new_relative'),6)
        identity=entry['identity']
        if (not all(key in identity for key in IDENTITY) or
                identity.get('role') not in ('empty','complete','solution') or
                old.parts[2]!='variant_'+str(identity.get('variant_id')) or
                new.parts[4]!='variant_'+str(identity.get('variant_id')) or
                old.suffix!='.png' or new.suffix!='.png'):
            raise ValueError('Named migration identity does not match its paths')
        self._path(str(old));self._path(str(new))

    def _verified(self,path,digest):
        return path.is_file() and sha256(path)==digest

    def index_matches(self,actual_digest):
        return actual_digest in {value for value in (self.previous.get('index_sha256'),self.prior,self.intended) if _digest(value)}

    def check_index(self):
        path=self.plain(self.root/INDEX)
        if path.exists() and (not path.is_file() or not self.index_matches(sha256(path))):
            raise ValueError('Named CSV index conflicts with an untracked or edited file; legacy exports were not moved')

    def plan(self,record,source,target,state):
        source=self.plain(source);target=self.plain(target)
        relative=target.relative_to(self.root).as_posix();_relative(relative,6)
        if record.get('relative_path')!=relative:raise ValueError('Named migration target disagrees with its record')
        digest=record.get('source_sha256');identity=_identity(record)
        if not _digest(digest) or record.get('destination_sha256')!=digest:
            raise ValueError('Named migration has no verified current image hash')
        key=_key(identity,digest)
        if key is None:raise ValueError('Invalid current named migration identity')
        pending=self.pending.get(key,[])
        candidates=[]
        if pending:
            if len(pending)!=1 or pending[0]['new_relative']!=relative:
                raise ValueError('Pending named migration disagrees with the current grouped destination')
            candidates=pending
        else:
            for old_record in self.legacy.get(key,[]):
                old_relative=old_record['relative_path']
                candidates.append({'old_relative':old_relative,'new_relative':relative,'digest':digest,'identity':identity})
        if not candidates:return None
        # Multiple identical historical aliases remain independent history. One
        # deterministic owned alias is sufficient for a no-copy layout upgrade.
        entry=candidates[0];self._validate(entry)
        old=self._path(entry['old_relative']);new=self._path(entry['new_relative'])
        if old.exists():
            if not self._verified(old,digest):raise ValueError('Legacy named destination conflicts with an edited file')
            if os.path.samefile(source,old):raise ValueError('Legacy named export is hardlinked to its raw source')
            if new.exists():
                if self._verified(new,digest) and not os.path.samefile(source,new):
                    self.cancelled.add(entry['old_relative']);return None
                # An existing grouped destination is handled by the normal
                # ownership/copy path, never overwritten by a legacy move.
                return None
        elif not self._verified(new,digest):
            if pending:raise ValueError('Pending named migration is missing both verified export paths')
            return None
        if new.exists() and os.path.samefile(source,new):raise ValueError('Grouped named export is hardlinked to its raw source')
        return {**entry,'record':record,'source':source,'target':target,'moved':False}

    def _save(self):
        journal={'schema':1,'previous_index_sha256':self.prior,'intended_index_sha256':self.intended,
                 'entries':list(self.entries.values())}
        raw=(json.dumps(journal,indent=2,ensure_ascii=True)+'\n').encode('utf-8')
        self.atomic_bytes(self.path,raw);self.journal_hash=hashlib.sha256(raw).hexdigest()

    def prepare(self,moves):
        entries={key:value for key,value in self.entries.items() if key not in self.cancelled}
        for move in moves:
            entry={key:move[key] for key in ('old_relative','new_relative','digest','identity')};self._validate(entry)
            old=entry['old_relative']
            if old in entries and entries[old]!=entry:raise ValueError('Legacy named migration plans conflict')
            entries[old]=entry
        targets=[entry['new_relative'] for entry in entries.values()]
        if len(targets)!=len(set(targets)):raise ValueError('Named migration plans target the same file')
        self.entries=entries
        self._index_pending()
        if entries or self.journal_hash is not None:self._save()

    def apply(self,move):
        entry=self.entries.get(move['old_relative'])
        if not entry or any(move[key]!=entry[key] for key in ('new_relative','digest','identity')):
            raise ValueError('Named migration was not journaled before its move')
        old=self._path(entry['old_relative']);new=self._path(entry['new_relative']);digest=entry['digest']
        source=self.plain(move['source'])
        if new.exists():
            if not self._verified(new,digest) or os.path.samefile(source,new):
                raise ValueError('Grouped named destination changed before migration')
            if old.exists():
                if not self._verified(old,digest):raise ValueError('Legacy named destination changed before migration')
                self.cancelled.add(entry['old_relative']);return None
            return entry['old_relative']
        if not self._verified(old,digest) or os.path.samefile(source,old):
            raise ValueError('Legacy named destination changed or is linked to its raw source')
        new.parent.mkdir(parents=True,exist_ok=True);old=self._path(entry['old_relative']);new=self._path(entry['new_relative'])
        if new.exists():raise ValueError('Grouped named destination appeared during migration')
        os.replace(old,new);move['moved']=True
        if not self._verified(new,digest):raise ValueError('Named export changed during its layout move')
        return entry['old_relative']

    def publishing(self,index_sha256):
        if not _digest(index_sha256):raise ValueError('Invalid grouped CSV index hash')
        self.intended=index_sha256
        if self.journal_hash is not None:self._save()

    def finish(self,ownership):
        if self.journal_hash is None:return
        for entry in self.entries.values():
            old=self._path(entry['old_relative']);new=self._path(entry['new_relative']);digest=entry['digest']
            if ownership.get(entry['new_relative'])!=digest or not self._verified(new,digest):
                raise ValueError('Named migration is incomplete; journal retained for recovery')
            if entry['old_relative'] in self.cancelled:
                if not self._verified(old,digest):raise ValueError('Retained legacy alias changed during migration')
            elif old.exists():raise ValueError('Named migration still has an unmoved legacy alias')
        if sha256(self.path)!=self.journal_hash:raise ValueError('Named migration journal changed during export')
        for entry in self.entries.values():
            if entry['old_relative'] in self.cancelled:continue
            directory=self._path(entry['old_relative']).parent
            # Only the three now-empty legacy grouping directories qualify.
            # Never traverse/delete nonempty folders or the named export root.
            for _ in range(3):
                try:
                    directory=self.plain(directory)
                    if directory==self.root or not directory.is_relative_to(self.root):break
                    directory.rmdir()
                except (OSError,ValueError):break
                directory=directory.parent
        self.plain(self.path).unlink()
