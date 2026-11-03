"""Unity bundle to in-memory AssetStudio-style JSON/PNG adapter.
Requires UnityPy. Custom MonoBehaviour typetrees must be embedded in the bundle.
"""
import io
import json
from pathlib import Path


def _serializable(value):
    if isinstance(value, bytes):
        return list(value)
    if isinstance(value, dict):
        return {str(k): _serializable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_serializable(v) for v in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    try:
        return _serializable(vars(value))
    except Exception:
        return str(value)


def _mono_name(tree, obj):
    name = tree.get('m_Name') or tree.get('name')
    if name:
        return str(name)
    try:
        script = obj.read().m_Script.read()
        return str(script.m_ClassName)
    except Exception:
        return ''


def extract_bundle(path):
    try:
        import UnityPy
    except ImportError as exc:
        raise RuntimeError('Bundle input requires UnityPy. Install it with: py -m pip install UnityPy Pillow') from exc

    path = Path(path)
    if path.is_dir():
        bundles = sorted(p for p in path.rglob('*') if p.is_file() and p.suffix.lower() in ('.bundle', '.unity3d', '.assetbundle'))
    else:
        bundles = [path]
    if not bundles:
        raise ValueError('No Unity bundle found')
    files = {}
    problems = []
    counters = {'MonoBehaviour': 0, 'Texture2D': 0, 'GAFAsset': 0, 'GAFObject': 0}
    for bundle in bundles:
        try:
            env = UnityPy.load(str(bundle))
        except Exception as exc:
            problems.append(f'{bundle.name}: cannot load bundle: {exc}')
            continue
        for obj in env.objects:
            typ = obj.type.name
            if typ == 'MonoBehaviour':
                counters['MonoBehaviour'] += 1
                try:
                    tree = obj.read_typetree()
                    tree = _serializable(tree)
                    if not isinstance(tree, dict):
                        continue
                    name = _mono_name(tree, obj)
                    # The original AssetStudio export stores GAFObject data in m_Data.
                    if isinstance(tree.get('m_Data'), dict) and 'm_AtlasInitialElementID' in tree['m_Data']:
                        counters['GAFObject'] += 1
                        name = 'GAFObject'
                    elif 'm_AssetData' in tree:
                        counters['GAFAsset'] += 1
                        name = str(tree.get('m_Name') or 'GAFAsset')
                    # Preserve all readable MonoBehaviours; the renderer selects relevant records.
                    fname = f'{name or "MonoBehaviour"}_{obj.path_id}.json'
                    files[fname] = json.dumps(tree, ensure_ascii=False).encode('utf-8')
                except Exception as exc:
                    problems.append(f'MonoBehaviour {obj.path_id}: cannot read typetree: {exc}')
            elif typ == 'Texture2D':
                counters['Texture2D'] += 1
                try:
                    texture = obj.read()
                    image = texture.image
                    out = io.BytesIO()
                    image.save(out, format='PNG')
                    name = str(texture.m_Name or 'Texture')
                    files[f'{name}_{obj.path_id}.png'] = out.getvalue()
                except Exception as exc:
                    problems.append(f'Texture2D {obj.path_id}: {exc}')
    if not files:
        raise ValueError('No readable assets found. ' + '; '.join(problems[:5]))
    return files, {'bundle_files': [str(p) for p in bundles], 'extracted': counters, 'issues': problems}
