using System;
using System.Collections.Generic;
using System.IO;
using UnityEditor;
using UnityEngine;

namespace OfSf.BuildReview
{
    public sealed class BuildCompareWindow : EditorWindow
    {
        [SerializeField] private string reportPath;
        [SerializeField] private string selected;
        private BuildReport report;
        private string[] labels;
        private string error;
        private Vector2 scroll;

        [MenuItem("Tools/OF_SF/客户端构建对照")]
        private static void ShowMenu() { Open(); }

        public static BuildCompareWindow Open()
        {
            var window = GetWindow<BuildCompareWindow>();
            window.titleContent = new GUIContent("构建对照");
            window.minSize = new Vector2(520, 340);
            return window;
        }

        public void OpenReport(string path)
        {
            var next = BuildReport.Load(path);
            var names = new List<string>(next.Clients.Keys);
            names.Sort(StringComparer.Ordinal);
            names.Remove(next.Reference);
            names.Insert(0, next.Reference);
            if (selected == null || !next.Clients.ContainsKey(selected))
                selected = names.Count > 1 ? names[1] : next.Reference;
            labels = names.ToArray();
            report = next;
            reportPath = path;
            error = null;
            scroll = Vector2.zero;
            Repaint();
        }

        private void OnEnable()
        {
            if (!String.IsNullOrEmpty(reportPath)) TryOpen(reportPath);
        }

        private void TryOpen(string path)
        {
            try { OpenReport(path); }
            catch (Exception ex) { error = "读取失败：" + ex.Message; }
        }

        private void OnGUI()
        {
            using (new EditorGUILayout.HorizontalScope(EditorStyles.toolbar))
            {
                if (GUILayout.Button("打开报告…", EditorStyles.toolbarButton, GUILayout.ExpandWidth(false)))
                {
                    string directory = String.IsNullOrEmpty(reportPath) ? "" : Path.GetDirectoryName(reportPath);
                    string path = EditorUtility.OpenFilePanel("选择构建报告", directory, "json");
                    if (!String.IsNullOrEmpty(path)) TryOpen(path);
                }
                using (new EditorGUI.DisabledScope(String.IsNullOrEmpty(reportPath)))
                    if (GUILayout.Button("重新读取", EditorStyles.toolbarButton, GUILayout.ExpandWidth(false))) TryOpen(reportPath);
                GUILayout.FlexibleSpace();
                if (report != null) GUILayout.Label(Path.GetFileName(reportPath), EditorStyles.miniLabel);
            }
            if (error != null) EditorGUILayout.HelpBox(error, MessageType.Error);
            if (report == null)
            {
                EditorGUILayout.HelpBox("打开 inspect_client_build.py 生成的 builds.json。", MessageType.Info);
                return;
            }
            scroll = EditorGUILayout.BeginScrollView(scroll);
            int choice = EditorGUILayout.Popup("对照样本", Array.IndexOf(labels, selected), labels);
            selected = labels[choice];
            var reference = report.Clients[report.Reference];
            var sample = report.Clients[selected];
            EditorGUILayout.Space();
            using (new EditorGUILayout.HorizontalScope())
            {
                using (new EditorGUILayout.VerticalScope(EditorStyles.helpBox))
                    DrawSample("参考样本", report.Reference, reference);
                if (selected != report.Reference)
                    using (new EditorGUILayout.VerticalScope(EditorStyles.helpBox))
                        DrawSample("对照样本", selected, sample);
            }
            if (selected != report.Reference)
            {
                var diff = report.Differences[selected];
                EditorGUILayout.Space();
                GUILayout.Label("有效资源包差异", EditorStyles.boldLabel);
                EditorGUILayout.LabelField("同名同摘要", diff.Unchanged.ToString("N0"));
                EditorGUILayout.LabelField("同名不同摘要", diff.Changed.ToString("N0"));
                EditorGUILayout.LabelField("对照样本新增 / 移除", diff.Added.ToString("N0") + " / " + diff.Removed.ToString("N0"));
            }
            DrawCode(report.Reference, reference.Effective);
            if (selected != report.Reference) DrawCode(selected, sample.Effective);
            EditorGUILayout.EndScrollView();
        }

        private static void DrawSample(string role, string label, ClientSample sample)
        {
            GUILayout.Label(role + " · " + label, EditorStyles.boldLabel);
            GUILayout.Label("Unity " + sample.Unity + "  ·  " + sample.Backend, EditorStyles.wordWrappedLabel);
            GUILayout.Label("元数据头 " + (sample.Metadata.HasValue ? sample.Metadata.Value.ToString() : "未识别") +
                            "  ·  " + sample.Assemblies + " 个程序集", EditorStyles.wordWrappedMiniLabel);
            GUILayout.Space(5);
            GUILayout.Label("基础版本", EditorStyles.miniLabel);
            GUILayout.Label(sample.Builtin.Version);
            GUILayout.Label(sample.Cache == null ? "使用基础清单" :
                            sample.CacheSource == "provided" ? "配对缓存版本" : "安装缓存版本", EditorStyles.miniLabel);
            GUILayout.Label(sample.Effective.Version);
            GUILayout.Label(sample.Effective.Bundles.ToString("N0") + " 个包  ·  " +
                            sample.Effective.Assets.ToString("N0") + " 项资源", EditorStyles.wordWrappedMiniLabel);
        }

        private static void DrawCode(string label, Release release)
        {
            EditorGUILayout.Space();
            GUILayout.Label("代码容器 · " + label, EditorStyles.boldLabel);
            if (release.Code.Count == 0) GUILayout.Label("该清单未索引代码容器。", EditorStyles.miniLabel);
            foreach (var pair in release.Code)
            {
                var item = pair.Value;
                using (new EditorGUILayout.VerticalScope(EditorStyles.helpBox))
                {
                    GUILayout.Label(pair.Key, EditorStyles.wordWrappedLabel);
                    EditorGUILayout.HelpBox(item.Summary, item.Verified == null ? MessageType.Warning : MessageType.Info);
                    GUILayout.Label("清单 MD5  ·  " + item.Size.ToString("N0") + " 字节", EditorStyles.miniLabel);
                    EditorGUILayout.SelectableLabel(item.Md5, GUILayout.Height(EditorGUIUtility.singleLineHeight));
                    if (item.Mismatches != null)
                        foreach (var copy in item.Mismatches)
                            GUILayout.Label((copy.Key == "cache" ? "缓存副本" : "内置副本") + "  ·  " +
                                            (copy.Value == "size_mismatch" ? "长度不符" : "实际 MD5 " + copy.Value),
                                            EditorStyles.wordWrappedMiniLabel);
                }
            }
        }
    }
}
