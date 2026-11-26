"""Regenerates docs/command_inventory.json by reading the source with the AST (no Discord needed).
Run from anywhere:  python scripts/command_inventory.py"""
import ast, json, pathlib
root=pathlib.Path(__file__).resolve().parent.parent
import os; os.chdir(root)
root=pathlib.Path('.')
files=[p for p in sorted(root.rglob('*.py')) if 'tests' not in p.parts and p.name not in ('ai_approval.py','install_assets.py')]
def lit(n):
    try: return ast.literal_eval(n)
    except Exception: return None
def kw(call,name):
    for k in call.keywords:
        if k.arg==name: return lit(k.value)
def sig(fn, skip_first_is_self=False):
    a=fn.args; out=[]
    args=a.args[:]; defaults=[None]*(len(args)-len(a.defaults))+list(a.defaults)
    for arg,d in zip(args,defaults):
        ann=ast.unparse(arg.annotation) if arg.annotation else ''
        if arg.arg in('self','ctx','interaction','cls') or 'Context' in ann or 'Interaction' in ann: continue
        out.append(f"[{arg.arg}]" if d is not None else f"<{arg.arg}>")
    for arg,d in zip(a.kwonlyargs,a.kw_defaults):
        out.append(f"[{arg.arg}]" if d is not None else f"<{arg.arg}>")
    return ' '.join(out)
def perms(fn):
    s=[]
    for d in fn.decorator_list:
        t=ast.unparse(d)
        if any(k in t for k in('has_permissions','has_guild_permissions','is_owner','guild_only','admin_only','staff_only','mod_only','manage_','configured_owner_only','bot_owner','check(','checks.')): s.append(t.replace('commands.','').replace('app_commands.',''))
    return s
rows=[]
for p in files:
    tree=ast.parse(p.read_text(encoding='utf-8'))
    groupname={}   # function/var name -> qualified command name
    # iterate in source order so parents come before children
    funcs=[n for n in ast.walk(tree) if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef))]
    funcs.sort(key=lambda n:n.lineno)
    for n in funcs:
        for d in n.decorator_list:
            call=d if isinstance(d,ast.Call) else None
            f=call.func if call else d
            if not isinstance(f,ast.Attribute): continue
            attr=f.attr; base=ast.unparse(f.value)
            if attr not in('command','hybrid_command','group','hybrid_group'): continue
            name=(kw(call,'name') if call else None) or (lit(call.args[0]) if call and call.args and isinstance(lit(call.args[0]),str) else None) or n.name
            root_bases={'bot','commands','app_commands','tree','client','self.bot','self.client'}
            parent=groupname.get(base.split('.')[-1]) if base not in root_bases else None
            q=(parent+' '+name) if parent else name
            if attr in('group','hybrid_group'): groupname[n.name]=q
            if base in('app_commands','tree','client.tree','self.bot.tree','bot.tree') or (attr=='command' and base=='app_commands'): kind='slash'
            elif attr in('hybrid_command','hybrid_group'): kind='prefix+slash'
            else: kind='prefix'
            if parent and not (kind=='slash') :
                kind='sub-'+('group' if attr in('group','hybrid_group') else 'command')
            desc=(kw(call,'description') if call else None) or (ast.get_docstring(n) or '').strip().split('\n')[0]
            rows.append(dict(file=str(p),line=n.lineno,kind=kind,name=q,aliases=(kw(call,'aliases') if call else None) or [],desc=desc,sig=sig(n),perm=perms(n),fn=n.name,group=attr in('group','hybrid_group')))
# context menus + nova message-dispatch
for p in files:
    s=p.read_text(encoding='utf-8')
    import re
    for m in re.finditer(r'ContextMenu\(name="([^"]+)"',s): rows.append(dict(file=str(p),line=0,kind='context-menu',name=m.group(1),aliases=[],desc='Right-click a message → Apps',sig='',perm=[],fn='',group=False))
tree=ast.parse(open('nova.py',encoding='utf-8').read())
for n in ast.walk(tree):
    if isinstance(n,ast.Tuple) and len(n.elts)==2 and isinstance(n.elts[0],ast.Constant) and isinstance(n.elts[0].value,str) and isinstance(n.elts[1],ast.Name) and n.elts[1].id.startswith('handle_'):
        fn=n.elts[1].id
        doc=''
        for f in ast.walk(tree):
            if isinstance(f,(ast.AsyncFunctionDef,ast.FunctionDef)) and f.name==fn: doc=(ast.get_docstring(f) or '').split('\n')[0]; sg=sig(f)
        rows.append(dict(file='nova.py',line=n.lineno,kind='prefix (message handler)',name=n.elts[0].value,aliases=[],desc=doc,sig='',perm=[],fn=fn,group=False))
json.dump(rows,open('docs/command_inventory.json','w'),indent=1,ensure_ascii=False)
from collections import Counter
print(len(rows)); print(Counter(r['kind'] for r in rows))
for r in rows[:0]: print(r)
