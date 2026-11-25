# Windows workstation readiness

Collected on 26 November 2026 at 00:12 Europe/Warsaw. The machine reports
Apple MacBookPro15,2 hardware running Windows. This matches the platform in the
[earlier hardware baseline](hardware-speech-baseline.md).

| Measurement | Observed |
| --- | --- |
| Operating system | Windows 11 Pro, x86-64 |
| Processor | Intel Core i5-8279U at 2.40 GHz, 4 physical cores and 8 logical processors |
| Physical memory | 8,428,630,016 bytes reported by `Win32_ComputerSystem`. Windows could use 8,231,084 KiB (about 7.85 GiB), with 33,188 KiB (about 32 MiB) free at sampling. |
| System volume | 275,005,829,120 bytes total (about 256 GiB), with 13,545,250,816 bytes free (about 12.62 GiB) |
| Docker daemon | Responded with Linux x86-64, 8 allocated CPUs, and 4,026,175,488 bytes of memory (about 3.75 GiB) |

The earlier inventory recorded about 804 MiB of available physical memory and
15.5 GiB of free system-volume space. Free resources vary with workload. A
separate invocation of the replacement rehearsal's memory check reported
15 MiB available and refused to proceed below its 2 GiB threshold. The Docker
allocation reports daemon resources. Measured Windows headroom was below the
replacement rehearsal gate, so this refresh stopped at read-only checks.
Capacity under load and inventories of a separate PC or macOS remain open.

## Repeat the checks

Run these from PowerShell. The commands return only hardware and resource fields.
Windows reports `Win32_Processor.Architecture` as `9` for x64. The memory fields
from `Win32_OperatingSystem` use KiB. Docker's memory field and disk sizes use bytes.

```powershell
Get-CimInstance Win32_Processor |
  Select-Object Name, Architecture, NumberOfCores, NumberOfLogicalProcessors
Get-CimInstance Win32_ComputerSystem |
  Select-Object Manufacturer, Model, TotalPhysicalMemory
Get-CimInstance Win32_OperatingSystem |
  Select-Object Caption, OSArchitecture, TotalVisibleMemorySize, FreePhysicalMemory
Get-CimInstance Win32_LogicalDisk -Filter 'DriveType=3' |
  Where-Object DeviceID -eq $env:SystemDrive |
  Select-Object Size, FreeSpace
docker info --format '{{.OSType}} {{.Architecture}} {{.NCPU}} {{.MemTotal}}'
python -c 'from scripts.rehearse_replacement_stack import require_windows_memory; require_windows_memory()'
```
