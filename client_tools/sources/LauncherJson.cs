using System;
using System.Collections.Generic;
using System.IO;
using System.Text.Json;
using System.Text.Json.Serialization;

namespace OverField.PrivateLauncher {
    internal sealed class LauncherJson {
        private static readonly JsonSerializerOptions Options = new() {
            Converters = { new ObjectValueConverter() }
        };

        public int MaxJsonLength { get; init; } = 2 * 1024 * 1024;
        public string Serialize<T>(T value) => JsonSerializer.Serialize(value, Options);

        public T Deserialize<T>(string text) {
            if (text.Length > MaxJsonLength) throw new InvalidDataException("数据长度超出限制。");
            return JsonSerializer.Deserialize<T>(text, Options);
        }

        private sealed class ObjectValueConverter : JsonConverter<object> {
            public override object Read(ref Utf8JsonReader reader, Type type, JsonSerializerOptions options) {
                return reader.TokenType switch {
                    JsonTokenType.StartObject => JsonSerializer.Deserialize<Dictionary<string, object>>(ref reader, options),
                    JsonTokenType.StartArray => JsonSerializer.Deserialize<List<object>>(ref reader, options),
                    JsonTokenType.Number when reader.TryGetInt64(out var number) => number,
                    JsonTokenType.Number => reader.GetDouble(),
                    JsonTokenType.String => reader.GetString(),
                    JsonTokenType.True => true,
                    JsonTokenType.False => false,
                    JsonTokenType.Null => null,
                    _ => throw new JsonException()
                };
            }

            public override void Write(Utf8JsonWriter writer, object value, JsonSerializerOptions options) {
                if (value.GetType() == typeof(object)) { writer.WriteStartObject(); writer.WriteEndObject(); }
                else JsonSerializer.Serialize(writer, value, value.GetType(), options);
            }
        }
    }
}
