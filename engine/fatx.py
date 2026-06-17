"""
fatx.py — Xbox FATX filesystem reader + focused writer.

Supports browsing the standard retail Xbox HDD partition layout and writing
files into a given path (used to inject E:\\TDATA\\fffe0000\\...).

The writer handles cluster allocation, FAT chain updates, and directory entry
creation. It is deliberately conservative: it grows directories within their
existing cluster chain and allocates fresh clusters from the free list.

NOTE: validate writes against a backup image before trusting them on a HDD you
care about. Always keep a backup of the original qcow2.
"""
import struct
import datetime

FATX_MAGIC = 0x58544146  # "FATX" little-endian
SUPERBLOCK_SIZE = 0x1000
DIRENT_SIZE = 0x40
DIRENT_FREE = 0x00
DIRENT_END = 0xFF
DIRENT_DELETED = 0xE5
ATTR_DIRECTORY = 0x10

# Standard retail Xbox HDD partition byte offsets and sizes.
# (offset, size) in bytes. These are the well-known fixed offsets.
PARTITIONS = {
    "X": (0x00080000, 0x02EE00000),
    "Y": (0x2EE80000, 0x02EE00000),
    "Z": (0x5DC80000, 0x02EE00000),
    "C": (0x8CA80000, 0x01F400000),
    "E": (0xABE80000, 0x131F00000),
}

# FAT chain end markers
FAT16_END = 0xFFFF
FAT16_BAD = 0xFFF7
FAT32_END = 0xFFFFFFFF
FAT32_BAD = 0xFFFFFFF7


class FatxError(Exception):
    pass


def _fatx_time_now():
    """Pack current time into a FATX timestamp (per aerosoul94 spec)."""
    now = datetime.datetime.now()
    ts = (now.year - 2000) & 0x7F
    ts |= (now.month & 0x0F) << 7
    ts |= (now.day & 0x1F) << 11
    ts |= (now.hour & 0x1F) << 16
    ts |= (now.minute & 0x3F) << 21
    ts |= ((now.second // 2) & 0x1F) << 27
    return ts


class FatxPartition:
    """Read/write access to a single FATX partition inside a raw image file."""

    def __init__(self, fileobj, part_offset, part_size):
        self.f = fileobj
        self.part_offset = part_offset
        self.part_size = part_size
        self._read_superblock()

    def _read_superblock(self):
        self.f.seek(self.part_offset)
        sb = self.f.read(SUPERBLOCK_SIZE)
        magic = struct.unpack_from("<I", sb, 0)[0]
        if magic != FATX_MAGIC:
            raise FatxError("Not a FATX partition (bad magic)")
        self.volume_id = struct.unpack_from("<I", sb, 4)[0]
        self.sectors_per_cluster = struct.unpack_from("<I", sb, 8)[0]
        self.cluster_size = self.sectors_per_cluster * 512
        self.root_dir_cluster = struct.unpack_from("<I", sb, 0x0C)[0] or 1

        # Layout math per the FATX spec (aerosoul94 / nxdk):
        #   MaxClusters = (PartitionLength / BytesPerCluster) + 1   <-- +1 reserved
        #   FAT16 if MaxClusters < 0xFFF0 else FAT32
        #   BytesPerFAT = (entry_size * MaxClusters) padded up to PageSize (0x1000)
        #   FileAreaOffset = 0x1000 + BytesPerFAT
        #   cluster N is at FileAreaOffset + (N - 1) * BytesPerCluster
        page = 0x1000
        self.max_clusters = (self.part_size // self.cluster_size) + 1
        self.num_clusters = self.max_clusters
        self.fat16 = self.max_clusters < 0xFFF0
        self.fat_entry_size = 2 if self.fat16 else 4

        self.fat_offset = self.part_offset + SUPERBLOCK_SIZE
        fat_bytes = self.fat_entry_size * self.max_clusters
        fat_bytes = (fat_bytes + (page - 1)) & ~(page - 1)
        self.fat_bytes = fat_bytes
        self.data_offset = self.fat_offset + fat_bytes
        # Cluster numbering starts at 1 (cluster 1 = first data cluster)

    # ---- FAT access ----

    def _fat_entry(self, cluster):
        pos = self.fat_offset + cluster * self.fat_entry_size
        self.f.seek(pos)
        if self.fat16:
            return struct.unpack("<H", self.f.read(2))[0]
        return struct.unpack("<I", self.f.read(4))[0]

    def _set_fat_entry(self, cluster, value):
        pos = self.fat_offset + cluster * self.fat_entry_size
        self.f.seek(pos)
        if self.fat16:
            self.f.write(struct.pack("<H", value & 0xFFFF))
        else:
            self.f.write(struct.pack("<I", value & 0xFFFFFFFF))

    def _is_end(self, entry):
        if self.fat16:
            return entry >= 0xFFF8
        return entry >= 0xFFFFFFF8

    def _end_marker(self):
        return FAT16_END if self.fat16 else FAT32_END

    def _cluster_offset(self, cluster):
        # Cluster 1 is the first data cluster
        return self.data_offset + (cluster - 1) * self.cluster_size

    def _chain(self, start_cluster):
        chain = []
        c = start_cluster
        seen = set()
        while c and not self._is_end(c):
            if c in seen:
                raise FatxError("Cyclic FAT chain")
            seen.add(c)
            chain.append(c)
            c = self._fat_entry(c)
        return chain

    def _alloc_cluster(self):
        # cluster 0 and 1 reserved; search from 1
        for c in range(1, self.num_clusters):
            if self._fat_entry(c) == 0:
                self._set_fat_entry(c, self._end_marker())
                return c
        raise FatxError("No free clusters")

    # ---- Directory parsing ----

    def _read_dir_entries(self, start_cluster):
        """Yield (entry_dict, location) for each used entry in a directory."""
        for cluster in self._chain(start_cluster):
            base = self._cluster_offset(cluster)
            self.f.seek(base)
            cluster_data = self.f.read(self.cluster_size)
            for i in range(0, self.cluster_size, DIRENT_SIZE):
                raw = cluster_data[i:i + DIRENT_SIZE]
                name_len = raw[0]
                if name_len == DIRENT_END:
                    return
                if name_len in (DIRENT_DELETED,) or name_len == 0:
                    continue
                attr = raw[1]
                name = raw[2:2 + name_len].decode("ascii", "replace")
                first_cluster, size = struct.unpack_from("<II", raw, 0x2C)
                yield {
                    "name": name,
                    "is_dir": bool(attr & ATTR_DIRECTORY),
                    "first_cluster": first_cluster,
                    "size": size,
                }, (cluster, i)

    def list_dir(self, path):
        """List entries at a path like '/TDATA/fffe0000'. Root cluster is 1."""
        cluster = self.root_dir_cluster
        if path.strip("/"):
            for part in path.strip("/").split("/"):
                found = None
                for entry, _loc in self._read_dir_entries(cluster):
                    if entry["name"].lower() == part.lower() and entry["is_dir"]:
                        found = entry
                        break
                if not found:
                    raise FatxError(f"Path not found: {path} (missing {part})")
                cluster = found["first_cluster"]
        return [e for e, _ in self._read_dir_entries(cluster)]

    def _find_entry(self, parent_cluster, name):
        for entry, loc in self._read_dir_entries(parent_cluster):
            if entry["name"].lower() == name.lower():
                return entry, loc
        return None, None

    def _resolve_dir_cluster(self, path):
        cluster = self.root_dir_cluster
        for part in [p for p in path.strip("/").split("/") if p]:
            entry, _ = self._find_entry(cluster, part)
            if not entry or not entry["is_dir"]:
                raise FatxError(f"Directory not found: {part}")
            cluster = entry["first_cluster"]
        return cluster

    # ---- File extraction ----

    def read_file(self, path):
        parts = [p for p in path.strip("/").split("/") if p]
        dir_path = "/".join(parts[:-1])
        fname = parts[-1]
        parent = self._resolve_dir_cluster("/" + dir_path)
        entry, _ = self._find_entry(parent, fname)
        if not entry or entry["is_dir"]:
            raise FatxError(f"File not found: {path}")
        data = bytearray()
        remaining = entry["size"]
        for cluster in self._chain(entry["first_cluster"]):
            self.f.seek(self._cluster_offset(cluster))
            chunk = self.f.read(min(self.cluster_size, remaining))
            data += chunk
            remaining -= len(chunk)
            if remaining <= 0:
                break
        return bytes(data)

    # ---- Writing ----

    def _write_chain_data(self, data):
        """Allocate a fresh cluster chain and write data into it. Returns first cluster."""
        n_clusters = max(1, (len(data) + self.cluster_size - 1) // self.cluster_size)
        clusters = []
        for _ in range(n_clusters):
            clusters.append(self._alloc_cluster())
        # Link chain
        for i in range(len(clusters) - 1):
            self._set_fat_entry(clusters[i], clusters[i + 1])
        self._set_fat_entry(clusters[-1], self._end_marker())
        # Write data
        for i, cluster in enumerate(clusters):
            chunk = data[i * self.cluster_size:(i + 1) * self.cluster_size]
            chunk = chunk.ljust(self.cluster_size, b"\x00")
            self.f.seek(self._cluster_offset(cluster))
            self.f.write(chunk)
        return clusters[0]

    def _add_dir_entry(self, parent_cluster, name, first_cluster, size, is_dir):
        """Add a directory entry in the parent directory's cluster chain."""
        name_bytes = name.encode("ascii")
        if len(name_bytes) > 42:
            raise FatxError(f"Name too long: {name}")
        ts = _fatx_time_now()
        attr = ATTR_DIRECTORY if is_dir else 0
        entry = struct.pack("<BB", len(name_bytes), attr)
        entry += name_bytes.ljust(42, b"\xff")
        entry += struct.pack("<II", first_cluster, size)
        entry += struct.pack("<III", ts, ts, ts)  # create/access/modify
        entry = entry.ljust(DIRENT_SIZE, b"\x00")

        # Find a free slot in the existing chain
        for cluster in self._chain(parent_cluster):
            base = self._cluster_offset(cluster)
            self.f.seek(base)
            cdata = self.f.read(self.cluster_size)
            for i in range(0, self.cluster_size, DIRENT_SIZE):
                marker = cdata[i]
                if marker in (DIRENT_FREE, DIRENT_END, DIRENT_DELETED):
                    self.f.seek(base + i)
                    self.f.write(entry)
                    # If we used the END slot, write a new END after if room
                    if marker == DIRENT_END and (i + DIRENT_SIZE) < self.cluster_size:
                        self.f.seek(base + i + DIRENT_SIZE)
                        self.f.write(b"\xff")
                    return
        # Need to extend the directory chain
        new_cluster = self._alloc_cluster()
        last = self._chain(parent_cluster)[-1]
        self._set_fat_entry(last, new_cluster)
        self._set_fat_entry(new_cluster, self._end_marker())
        base = self._cluster_offset(new_cluster)
        self.f.seek(base)
        self.f.write(b"\x00" * self.cluster_size)
        self.f.seek(base)
        self.f.write(entry)
        if DIRENT_SIZE < self.cluster_size:
            self.f.seek(base + DIRENT_SIZE)
            self.f.write(b"\xff")

    def ensure_dir(self, path):
        """Create directory path if missing. Returns the directory's first cluster."""
        cluster = self.root_dir_cluster
        for part in [p for p in path.strip("/").split("/") if p]:
            entry, _ = self._find_entry(cluster, part)
            if entry and entry["is_dir"]:
                cluster = entry["first_cluster"]
                continue
            if entry and not entry["is_dir"]:
                raise FatxError(f"Path component is a file, not dir: {part}")
            # Create the directory: allocate one empty cluster
            new_cluster = self._alloc_cluster()
            base = self._cluster_offset(new_cluster)
            self.f.seek(base)
            self.f.write(b"\x00" * self.cluster_size)
            # Mark it as an empty directory (first byte END)
            self.f.seek(base)
            self.f.write(b"\xff")
            self._add_dir_entry(cluster, part, new_cluster, 0, True)
            cluster = new_cluster
        return cluster

    def write_file(self, path, data, overwrite=True):
        """Write a file at path (e.g. '/TDATA/fffe0000/music/ST.DB')."""
        parts = [p for p in path.strip("/").split("/") if p]
        dir_path = "/" + "/".join(parts[:-1])
        fname = parts[-1]
        parent = self.ensure_dir(dir_path)

        existing, loc = self._find_entry(parent, fname)
        if existing:
            if not overwrite:
                raise FatxError(f"File exists: {path}")
            # Free old chain and clear directory entry, then re-add
            if existing["first_cluster"]:
                for c in self._chain(existing["first_cluster"]):
                    self._set_fat_entry(c, 0)
            cluster, idx = loc
            self.f.seek(self._cluster_offset(cluster) + idx)
            self.f.write(bytes([DIRENT_DELETED]))

        first_cluster = self._write_chain_data(data) if data else 0
        self._add_dir_entry(parent, fname, first_cluster, len(data), False)

    def _free_chain(self, first_cluster):
        """Mark every cluster in a chain as free (0) in the FAT."""
        if not first_cluster:
            return
        for c in self._chain(first_cluster):
            self._set_fat_entry(c, 0)

    def _mark_entry_deleted(self, loc):
        """Mark a directory entry as deleted (0xE5) at (cluster, byte index)."""
        cluster, idx = loc
        self.f.seek(self._cluster_offset(cluster) + idx)
        self.f.write(bytes([DIRENT_DELETED]))

    def delete_file(self, path):
        """Delete a file: free its cluster chain and mark its dirent deleted."""
        parts = [p for p in path.strip("/").split("/") if p]
        dir_path = "/" + "/".join(parts[:-1])
        fname = parts[-1]
        parent = self._resolve_dir_cluster(dir_path)
        entry, loc = self._find_entry(parent, fname)
        if not entry:
            raise FatxError(f"File not found: {path}")
        if entry["is_dir"]:
            raise FatxError(f"Not a file: {path}")
        self._free_chain(entry["first_cluster"])
        self._mark_entry_deleted(loc)

    def delete_dir(self, path, recursive=True):
        """
        Delete a directory. With recursive=True (default), removes all contents
        first. Frees all cluster chains and marks all directory entries deleted.
        """
        parts = [p for p in path.strip("/").split("/") if p]
        dir_path = "/" + "/".join(parts[:-1])
        dname = parts[-1]
        parent = self._resolve_dir_cluster(dir_path)
        entry, loc = self._find_entry(parent, dname)
        if not entry:
            raise FatxError(f"Directory not found: {path}")
        if not entry["is_dir"]:
            raise FatxError(f"Not a directory: {path}")

        dir_cluster = entry["first_cluster"]
        # Recurse into children
        children = list(self._read_dir_entries(dir_cluster))
        if children and not recursive:
            raise FatxError(f"Directory not empty: {path}")
        for child, child_loc in children:
            child_path = f"{path}/{child['name']}"
            if child["is_dir"]:
                self.delete_dir(child_path, recursive=True)
            else:
                self._free_chain(child["first_cluster"])
                self._mark_entry_deleted(child_loc)

        # Free the directory's own cluster chain and remove its entry in parent
        self._free_chain(dir_cluster)
        self._mark_entry_deleted(loc)


class XboxImage:
    """Top-level access to an Xbox HDD raw image with FATX partitions."""

    def __init__(self, path, writable=False):
        self.path = path
        self.f = open(path, "r+b" if writable else "rb")

    def partition(self, letter):
        if letter not in PARTITIONS:
            raise FatxError(f"Unknown partition {letter}")
        offset, size = PARTITIONS[letter]
        return FatxPartition(self.f, offset, size)

    def available_partitions(self):
        found = []
        for letter, (offset, size) in PARTITIONS.items():
            self.f.seek(offset)
            magic = struct.unpack("<I", self.f.read(4))[0]
            if magic == FATX_MAGIC:
                found.append(letter)
        return found

    def close(self):
        self.f.close()
