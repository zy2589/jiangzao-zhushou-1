using System;
using System.Diagnostics;
using System.IO;
using System.IO.Compression;
using System.Text;
using System.Threading;
using System.Windows.Forms;

namespace DenoiseAssistantBootstrap
{
    internal static class Program
    {
        private const int FooterLength = 83;
        private const string FooterMagic = "DENOISEZIP1";

        [STAThread]
        private static int Main()
        {
            Mutex mutex = null;
            bool ownsMutex = false;
            string stagingDirectory = null;
            try
            {
                string self = Process.GetCurrentProcess().MainModule.FileName;
                string cacheRoot = Path.Combine(
                    Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
                    "DenoiseAssistant", "Runtime");
                string hash;
                long payloadOffset;
                long payloadLength;

                using (FileStream executable = new FileStream(self, FileMode.Open, FileAccess.Read, FileShare.Read))
                {
                    if (executable.Length < FooterLength)
                        throw new InvalidDataException("应用包不完整。");
                    executable.Position = executable.Length - FooterLength;
                    byte[] footer = new byte[FooterLength];
                    ReadExactly(executable, footer, 0, footer.Length);
                    payloadLength = BitConverter.ToInt64(footer, 0);
                    hash = Encoding.ASCII.GetString(footer, 8, 64);
                    string magic = Encoding.ASCII.GetString(footer, 72, FooterMagic.Length);
                    if (magic != FooterMagic || payloadLength <= 0)
                        throw new InvalidDataException("无法识别应用包格式。");
                    payloadOffset = executable.Length - FooterLength - payloadLength;
                    if (payloadOffset < 0)
                        throw new InvalidDataException("应用包长度信息无效。");
                }

                string versionDirectory = Path.Combine(cacheRoot, hash);
                string readyFile = Path.Combine(versionDirectory, ".ready");
                string appExecutable = Path.Combine(versionDirectory, "降噪助手.exe");
                Directory.CreateDirectory(cacheRoot);
                mutex = new Mutex(false, "Local\\DenoiseAssistantCache_" + hash);
                try { ownsMutex = mutex.WaitOne(); }
                catch (AbandonedMutexException) { ownsMutex = true; }

                if (!File.Exists(readyFile) || !File.Exists(appExecutable))
                {
                    stagingDirectory = Path.Combine(cacheRoot, ".extract-" + Guid.NewGuid().ToString("N"));
                    Directory.CreateDirectory(stagingDirectory);
                    ExtractPayload(self, payloadOffset, payloadLength, stagingDirectory);
                    File.WriteAllText(Path.Combine(stagingDirectory, ".ready"), hash, Encoding.ASCII);
                    if (Directory.Exists(versionDirectory))
                        Directory.Delete(versionDirectory, true);
                    Directory.Move(stagingDirectory, versionDirectory);
                    stagingDirectory = null;
                }

                ProcessStartInfo startInfo = new ProcessStartInfo();
                startInfo.FileName = appExecutable;
                startInfo.WorkingDirectory = versionDirectory;
                startInfo.UseShellExecute = false;
                Process.Start(startInfo);
                return 0;
            }
            catch (Exception error)
            {
                MessageBox.Show(
                    "降噪助手启动失败。\r\n\r\n" + error.Message,
                    "降噪助手", MessageBoxButtons.OK, MessageBoxIcon.Error);
                return 1;
            }
            finally
            {
                if (stagingDirectory != null)
                {
                    try { Directory.Delete(stagingDirectory, true); }
                    catch { }
                }
                if (ownsMutex && mutex != null)
                {
                    try { mutex.ReleaseMutex(); }
                    catch { }
                }
                if (mutex != null)
                    mutex.Dispose();
            }
        }

        private static void ExtractPayload(string executablePath, long offset, long length, string destination)
        {
            using (FileStream executable = new FileStream(executablePath, FileMode.Open, FileAccess.Read, FileShare.Read))
            using (RegionStream payload = new RegionStream(executable, offset, length))
            using (ZipArchive archive = new ZipArchive(payload, ZipArchiveMode.Read, true))
            {
                string root = Path.GetFullPath(destination).TrimEnd(Path.DirectorySeparatorChar) + Path.DirectorySeparatorChar;
                foreach (ZipArchiveEntry entry in archive.Entries)
                {
                    string relative = entry.FullName.Replace('/', Path.DirectorySeparatorChar);
                    string outputPath = Path.GetFullPath(Path.Combine(destination, relative));
                    if (!outputPath.StartsWith(root, StringComparison.OrdinalIgnoreCase))
                        throw new InvalidDataException("应用包包含无效路径。");
                    if (entry.FullName.EndsWith("/", StringComparison.Ordinal))
                    {
                        Directory.CreateDirectory(outputPath);
                        continue;
                    }
                    string parent = Path.GetDirectoryName(outputPath);
                    if (!Directory.Exists(parent))
                        Directory.CreateDirectory(parent);
                    using (Stream input = entry.Open())
                    using (FileStream output = new FileStream(outputPath, FileMode.CreateNew, FileAccess.Write, FileShare.None))
                        input.CopyTo(output);
                }
            }
        }

        private static void ReadExactly(Stream stream, byte[] buffer, int offset, int count)
        {
            while (count > 0)
            {
                int read = stream.Read(buffer, offset, count);
                if (read <= 0)
                    throw new EndOfStreamException("应用包结尾不完整。");
                offset += read;
                count -= read;
            }
        }

        private sealed class RegionStream : Stream
        {
            private readonly Stream source;
            private readonly long start;
            private readonly long length;

            public RegionStream(Stream source, long start, long length)
            {
                this.source = source;
                this.start = start;
                this.length = length;
                this.source.Position = start;
            }

            public override bool CanRead { get { return true; } }
            public override bool CanSeek { get { return true; } }
            public override bool CanWrite { get { return false; } }
            public override long Length { get { return length; } }
            public override long Position
            {
                get { return source.Position - start; }
                set { Seek(value, SeekOrigin.Begin); }
            }

            public override int Read(byte[] buffer, int offset, int count)
            {
                long remaining = length - Position;
                if (remaining <= 0)
                    return 0;
                if (count > remaining)
                    count = (int)remaining;
                return source.Read(buffer, offset, count);
            }

            public override long Seek(long offset, SeekOrigin origin)
            {
                long target;
                if (origin == SeekOrigin.Begin)
                    target = offset;
                else if (origin == SeekOrigin.Current)
                    target = Position + offset;
                else
                    target = length + offset;
                if (target < 0 || target > length)
                    throw new IOException("应用包读取位置超出范围。");
                source.Position = start + target;
                return target;
            }

            public override void Flush() { }
            public override void SetLength(long value) { throw new NotSupportedException(); }
            public override void Write(byte[] buffer, int offset, int count) { throw new NotSupportedException(); }
        }
    }
}
