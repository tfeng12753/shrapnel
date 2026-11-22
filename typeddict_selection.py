"""Conservative local base resolution for the inherited TypedDict experiment."""
import ast
import copy
import re


def dynamic_metadata_readers(source, roots):
    """Unresolved reflection may consume metadata without spelling its name."""
    reflection = re.compile(r'\b(?:getattr|setattr|vars|globals|locals|eval|exec|attrgetter|getmembers|getattr_static)\b'
                            r'|__dict__|__getattribute__')
    try:
        return any(reflection.search(path.read_text(encoding='utf-8', errors='replace'))
                   for root in roots for path in (source / root).rglob('*.py'))
    except OSError:
        return True


def inherited_total_only(before, after):
    """Prove local TypedDict ancestry and reject every other AST change.

    Imports and simple aliases must be unique bindings. Conditional declarations,
    foreign bases, decorators, rebinding and computed bases remain unknown.
    """
    def declarations(tree):
        counts = {}
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
                names = [node.id]
            elif isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                names = [node.name]
            elif isinstance(node, (ast.Import, ast.ImportFrom)):
                names = [a.asname or a.name.split('.')[0] for a in node.names]
            for name in names:
                counts[name] = counts.get(name, 0) + 1
            if isinstance(node, ast.ImportFrom) and any(a.name == '*' for a in node.names):
                return {}
            if isinstance(node, ast.Attribute) and isinstance(node.ctx, (ast.Store, ast.Del)):
                return {}
        known, classes = {}, {}

        def resolve(node):
            if isinstance(node, ast.Name):
                return known.get(node.id)
            if (isinstance(node, ast.Attribute) and node.attr == 'TypedDict'
                    and isinstance(node.value, ast.Name)
                    and known.get(node.value.id) == 'typing'):
                return 'factory'
            return None

        for node in tree.body:
            if isinstance(node, ast.Import):
                for alias in node.names:
                    name = alias.asname or alias.name
                    if alias.name in ('typing', 'typing_extensions') and counts.get(name) == 1:
                        known[name] = 'typing'
            elif isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    name = alias.asname or alias.name
                    if (node.level == 0 and node.module in ('typing', 'typing_extensions')
                            and alias.name == 'TypedDict' and counts.get(name) == 1):
                        known[name] = 'factory'
            elif (isinstance(node, ast.Assign) and len(node.targets) == 1
                  and isinstance(node.targets[0], ast.Name)):
                name = node.targets[0].id
                if counts.get(name) == 1 and resolve(node.value):
                    known[name] = resolve(node.value)
            elif isinstance(node, ast.ClassDef):
                bases = [resolve(base) for base in node.bases]
                if (counts.get(node.name) != 1 or not bases
                        or any(base not in ('factory', 'class') for base in bases)
                        or node.decorator_list or getattr(node, 'type_params', ())
                        or any(k.arg != 'total' or not isinstance(k.value, ast.Constant)
                               or type(k.value.value) is not bool for k in node.keywords)
                        or any(not (isinstance(item, ast.Pass)
                                    or isinstance(item, ast.AnnAssign) and item.value is None
                                    or isinstance(item, ast.Expr) and isinstance(item.value, ast.Constant)
                                    and isinstance(item.value.value, str)) for item in node.body)):
                    continue
                known[node.name] = 'class'
                classes[node.name] = (node, 'class' in bases)
        return classes

    left, right = declarations(before), declarations(after)
    normalized = copy.deepcopy(after)
    inherited = False
    for node in normalized.body:
        if not isinstance(node, ast.ClassDef) or node.name not in left or node.name not in right:
            continue
        old, old_inherited = left[node.name]
        new, new_inherited = right[node.name]
        if len(old.keywords) != len(new.keywords):
            continue
        for a, b, target in zip(old.keywords, new.keywords, node.keywords):
            if a.arg == b.arg == 'total' and a.value.value != b.value.value:
                target.value = copy.deepcopy(a.value)
                inherited |= old_inherited and new_inherited
    return inherited and ast.dump(before) == ast.dump(normalized)
