from pathlib import Path
import os

from django.core.files.storage import FileSystemStorage
from django.utils.deconstruct import deconstructible


@deconstructible
class AndroidSafeFileSystemStorage(FileSystemStorage):
    """File storage that avoids POSIX flock() on Android/Termux shared storage.

    Django's default FileSystemStorage uses fcntl.flock while saving files.
    Some Android shared-storage mounts report ENOSYS (Function not implemented),
    which turns an otherwise valid upload into HTTP 500. We still use O_EXCL
    for collision-safe creation; this backend simply omits the unsupported
    advisory lock.
    """
    def _save(self, name, content):
        full_path = self.path(name)
        directory = os.path.dirname(full_path)
        os.makedirs(directory, exist_ok=True)

        while True:
            try:
                # Never call Django's file_move_safe() here: it uses fcntl/flock
                # and Android shared-storage mounts can return ENOSYS. Copy into
                # an O_EXCL-created destination instead.
                flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
                if hasattr(os, 'O_BINARY'):
                    flags |= os.O_BINARY
                fd = os.open(full_path, flags, 0o666)
                try:
                    with os.fdopen(fd, 'wb') as _file:
                        fd = None
                        if hasattr(content, 'temporary_file_path'):
                            import shutil
                            with open(content.temporary_file_path(), 'rb') as src:
                                shutil.copyfileobj(src, _file, length=1024 * 1024)
                        else:
                            for chunk in content.chunks():
                                _file.write(chunk)
                finally:
                    if fd is not None:
                        os.close(fd)
            except FileExistsError:
                name = self.get_available_name(name)
                full_path = self.path(name)
                continue
            break

        try:
            self.file_permissions_mode and os.chmod(full_path, self.file_permissions_mode)
        except PermissionError:
            pass
        return str(name).replace(os.sep, '/')
