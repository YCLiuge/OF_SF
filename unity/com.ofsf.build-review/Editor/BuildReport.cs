using System;
using System.Collections.Generic;
using System.IO;
using System.Runtime.Serialization;
using System.Runtime.Serialization.Json;
using System.Text;

namespace OfSf.BuildReview
{
    [DataContract]
    public sealed class BuildReport
    {
        [DataMember(Name = "schema", IsRequired = true)] public int Schema;
        [DataMember(Name = "reference", IsRequired = true)] public string Reference;
        [DataMember(Name = "clients", IsRequired = true)] public Dictionary<string, ClientSample> Clients;
        [DataMember(Name = "effective_bundle_diff", IsRequired = true)] public Dictionary<string, BundleDiff> Differences;

        public static BuildReport Load(string path)
        {
            var options = new DataContractJsonSerializerSettings { UseSimpleDictionaryFormat = true, MaxItemsInObjectGraph = 200000 };
            var serializer = new DataContractJsonSerializer(typeof(BuildReport), options);
            BuildReport report;
            using (var file = File.OpenRead(path))
            {
                if (file.Length > 8 * 1024 * 1024)
                    throw new InvalidDataException("文件超过 8 MiB，请选择 inspect_client_build.py 生成的报告。");
                // PowerShell can write reports as UTF-16 or UTF-8 with a byte-order mark.
                using (var reader = new StreamReader(file, new UTF8Encoding(false, true), true))
                using (var json = new MemoryStream(Encoding.UTF8.GetBytes(reader.ReadToEnd())))
                    report = (BuildReport)serializer.ReadObject(json);
            }
            if (report == null || report.Schema != 1 || report.Clients == null || report.Clients.Count == 0 ||
                String.IsNullOrEmpty(report.Reference) || !report.Clients.ContainsKey(report.Reference) || report.Differences == null)
                throw new InvalidDataException("报告缺少有效的版本或参考样本。");
            foreach (var pair in report.Clients)
            {
                var sample = pair.Value;
                if (sample == null || String.IsNullOrEmpty(sample.Unity) || sample.Builtin == null ||
                    !sample.Builtin.Valid() || (sample.Cache != null && !sample.Cache.Valid()) ||
                    (pair.Key != report.Reference && (!report.Differences.ContainsKey(pair.Key) || report.Differences[pair.Key] == null)))
                    throw new InvalidDataException("样本数据不完整：" + pair.Key);
            }
            return report;
        }
    }

    [DataContract]
    public sealed class ClientSample
    {
        [DataMember(Name = "unity", IsRequired = true)] public string Unity;
        [DataMember(Name = "backend", IsRequired = true)] public string Backend;
        [DataMember(Name = "metadata_header_version")] public int? Metadata;
        [DataMember(Name = "declared_assemblies", IsRequired = true)] public int Assemblies;
        [DataMember(Name = "builtin", IsRequired = true)] public Release Builtin;
        [DataMember(Name = "cache")] public Release Cache;
        [DataMember(Name = "cache_source")] public string CacheSource;
        public Release Effective { get { return Cache ?? Builtin; } }
    }

    [DataContract]
    public sealed class Release
    {
        [DataMember(Name = "version", IsRequired = true)] public string Version;
        [DataMember(Name = "bundles", IsRequired = true)] public int Bundles;
        [DataMember(Name = "assets", IsRequired = true)] public int Assets;
        [DataMember(Name = "indexed_code_assets", IsRequired = true)] public Dictionary<string, CodeAsset> Code;

        internal bool Valid()
        {
            if (String.IsNullOrEmpty(Version) || Bundles <= 0 || Assets <= 0 || Code == null)
                return false;
            foreach (var entry in Code.Values)
                if (entry == null || String.IsNullOrEmpty(entry.Md5))
                    return false;
            return true;
        }
    }

    [DataContract]
    public sealed class CodeAsset
    {
        [DataMember(Name = "container_md5", IsRequired = true)] public string Md5;
        [DataMember(Name = "container_bytes", IsRequired = true)] public long Size;
        [DataMember(Name = "verified_local")] public string Verified;
        [DataMember(Name = "mismatches")] public Dictionary<string, string> Mismatches;
        public string Summary
        {
            get
            {
                if (Verified == "cache") return "缓存副本已通过校验";
                if (Verified == "builtin") return "内置副本已通过校验";
                return Mismatches != null && Mismatches.Count != 0 ? "本地副本校验失败" : "未找到本地副本";
            }
        }
    }

    [DataContract]
    public sealed class BundleDiff
    {
        [DataMember(Name = "unchanged", IsRequired = true)] public int Unchanged;
        [DataMember(Name = "changed", IsRequired = true)] public int Changed;
        [DataMember(Name = "added", IsRequired = true)] public int Added;
        [DataMember(Name = "removed", IsRequired = true)] public int Removed;
    }
}
