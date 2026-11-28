FILES = {
    'node.py': '''class File:
    def __init__(self, name, content=""):
        self.name = name
        self.content = content

class Dir:
    def __init__(self, name):
        self.name = name
        self.children = {}
''',
    'vfs.py': '''from node import File, Dir

class VFS:
    def mkdir(self, path):
        parts = path.strip('/').split('/')
        current = self.root
        for part in parts[:-1]:
            if part not in current.children:
                current.children[part] = Dir(part)
            current = current.children[part]
        if parts[-1] in current.children:
            raise KeyError(f'File {parts[-1]} already exists')
        current.children[parts[-1]] = Dir(parts[-1])

    def write(self, path, content):
        parts = path.strip('/').split('/')
        current = self.root
        for part in parts[:-1]:
            if part not in current.children:
                current.children[part] = Dir(part)
            current = current.children[part]
        if parts[-1] in current.children:
            if isinstance(current.children[parts[-1]], Dir):
                raise KeyError(f'Directory {parts[-1]} already exists')
            current.children[parts[-1]].content = content
        else:
            current.children[parts[-1]] = File(parts[-1], content)

    def read(self, path):
        parts = path.strip('/').split('/')
        current = self.root
        for part in parts:
            if part in current.children:
                current = current.children[part]
            else:
                raise KeyError(f'File or directory {path} not found')
        if isinstance(current, Dir):
            raise KeyError(f'Path {path} is a directory')
        return current.content

    def ls(self, path=""):
        parts = path.strip('/').split('/')
        current = self.root
        for part in parts:
            if part in current.children:
                current = current.children[part]
            else:
                raise KeyError(f'File or directory {path} not found')
        return sorted(current.children.keys())

    def rm(self, path):
        parts = path.strip('/').split('/')
        current = self.root
        parent = None
        for i, part in enumerate(parts):
            if part in current.children:
                if i == len(parts) - 1:
                    if isinstance(current.children[part], File):
                        del current.children[part]
                    else:
                        self._rm_dir(current.children[part])
                        del current.children[part]
                else:
                    parent = current
                    current = current.children[part]
            else:
                raise KeyError(f'File or directory {path} not found')

    def _rm_dir(self, dir):
        for child in dir.children.values():
            if isinstance(child, Dir):
                self._rm_dir(child)
            else:
                del dir.children[child.name]
        del self.root.children[dir.name]
''',
}