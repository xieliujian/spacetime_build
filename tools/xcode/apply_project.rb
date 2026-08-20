#!/usr/bin/env ruby
# frozen_string_literal: true

# 这是唯一允许被 Python XcodeProjectPlanApplier 调用的工程编辑入口。
# 输入只能是一个 workspace 内的 JSON 文件；所有字段均在本文件中显式白名单校验，
# 因此请求不能携带 Ruby 代码、命令行选项或任意脚本路径。

require "fileutils"
require "json"
require "rexml/document"
require "rexml/formatters/pretty"
require "securerandom"

TOP_LEVEL_KEYS = %w[operation project_path targets].freeze
TARGET_KEYS = %w[build_settings entitlements frameworks libraries name].freeze
SETTING_KEYS = %w[key value].freeze
LINK_KEYS = %w[name weak].freeze
ENTITLEMENT_KEYS = %w[key value].freeze

class XcodeProjectToolFailure < StandardError
end

def fail_tool(message)
  raise XcodeProjectToolFailure, message
end

def emit_failure(message)
  # 错误输出只包含固定摘要，不回显完整请求或工程内容。
  STDERR.write(JSON.generate({ "status" => "error", "message" => message }) + "\n")
  exit 1
end

def exact_keys!(value, allowed, label)
  fail_tool("#{label} must be an object") unless value.is_a?(Hash)
  keys = value.keys
  fail_tool("#{label} has unknown fields") unless keys.all? { |key| allowed.include?(key) }
  fail_tool("#{label} has missing fields") unless keys.sort == allowed.sort
end

def text!(value, label)
  fail_tool("#{label} must be a non-empty string") unless value.is_a?(String) && !value.empty?
  fail_tool("#{label} contains control characters") if value.match?(/[\x00-\x1f\x7f]/)
  value
end

def boolean!(value, label)
  fail_tool("#{label} must be boolean") unless value == true || value == false
  value
end

def scalar!(value, label)
  valid = value.is_a?(String) || value == true || value == false || value.is_a?(Integer)
  fail_tool("#{label} must be a plist scalar") unless valid
  text!(value, label) if value.is_a?(String)
  value
end

def relative_path!(value, label)
  path = text!(value, label)
  fail_tool("#{label} must be a relative POSIX path") if path.start_with?("/") || path.include?("\\")
  segments = path.split("/")
  fail_tool("#{label} contains an unsafe path segment") if segments.any? { |segment| segment.empty? || segment == "." || segment == ".." }
  path
end

def inside_workspace!(workspace, path, label)
  normalized_workspace = File.realpath(workspace)
  expanded_path = File.expand_path(path)
  normalized_path = if File.exist?(expanded_path)
                      File.realpath(expanded_path)
                    else
                      File.join(File.realpath(File.dirname(expanded_path)), File.basename(expanded_path))
                    end
  prefix = normalized_workspace.end_with?(File::SEPARATOR) ? normalized_workspace : normalized_workspace + File::SEPARATOR
  fail_tool("#{label} escapes workspace") unless normalized_path == normalized_workspace || normalized_path.start_with?(prefix)
  normalized_path
end

def validate_link_items!(items, label)
  fail_tool("#{label} must be an array") unless items.is_a?(Array)
  seen = {}
  items.each do |item|
    exact_keys!(item, LINK_KEYS, label)
    name = relative_path!(item["name"], "#{label}.name")
    weak = boolean!(item["weak"], "#{label}.weak")
    fail_tool("#{label} has a conflicting duplicate") if seen.key?(name) && seen[name] != weak
    seen[name] = weak
  end
end

def validate_request!(request)
  exact_keys!(request, TOP_LEVEL_KEYS, "request")
  fail_tool("unsupported operation") unless request["operation"] == "apply_xcode_project_plan"
  relative_path!(request["project_path"], "project_path")
  fail_tool("project_path must end with .xcodeproj") unless request["project_path"].end_with?(".xcodeproj")
  targets = request["targets"]
  fail_tool("targets must be an array") unless targets.is_a?(Array)
  target_names = {}
  targets.each do |target|
    exact_keys!(target, TARGET_KEYS, "target")
    name = text!(target["name"], "target.name")
    fail_tool("target.name contains a path separator") if name.include?("/") || name.include?("\\")
    fail_tool("duplicate target") if target_names.key?(name)
    target_names[name] = true

    settings = target["build_settings"]
    fail_tool("build_settings must be an array") unless settings.is_a?(Array)
    setting_names = {}
    settings.each do |setting|
      exact_keys!(setting, SETTING_KEYS, "build_setting")
      key = text!(setting["key"], "build_setting.key")
      value = text!(setting["value"], "build_setting.value")
      fail_tool("conflicting build setting") if setting_names.key?(key) && setting_names[key] != value
      setting_names[key] = value
    end

    validate_link_items!(target["frameworks"], "framework")
    validate_link_items!(target["libraries"], "library")
    framework_names = target["frameworks"].map { |item| item["name"] }
    library_names = target["libraries"].map { |item| item["name"] }
    fail_tool("framework/library conflict") unless (framework_names & library_names).empty?

    entitlements = target["entitlements"]
    fail_tool("entitlements must be an array") unless entitlements.is_a?(Array)
    entitlement_names = {}
    entitlements.each do |entitlement|
      exact_keys!(entitlement, ENTITLEMENT_KEYS, "entitlement")
      key = text!(entitlement["key"], "entitlement.key")
      value = scalar!(entitlement["value"], "entitlement.value")
      fail_tool("conflicting entitlement") if entitlement_names.key?(key) && entitlement_names[key] != value
      entitlement_names[key] = value
    end
  end
  request
end

def existing_file_reference(target, name)
  target.frameworks_build_phase.files.find do |build_file|
    reference = build_file.file_ref
    reference && (reference.path == name || File.basename(reference.path.to_s) == File.basename(name))
  end
end

def add_link!(project, target, item)
  return if existing_file_reference(target, item["name"])

  reference = project.frameworks_group.new_file(item["name"])
  target.frameworks_build_phase.add_file_reference(reference, item["weak"])
end

def entitlement_document(values)
  document = REXML::Document.new
  plist = document.add_element("plist", { "version" => "1.0" })
  dictionary = plist.add_element("dict")
  values.keys.sort.each do |key|
    dictionary.add_element("key").text = key
    value = values[key]
    if value == true || value == false
      dictionary.add_element(value ? "true" : "false")
    elsif value.is_a?(Integer)
      dictionary.add_element("integer").text = value.to_s
    else
      dictionary.add_element("string").text = value.to_s
    end
  end
  output = String.new
  REXML::Formatters::Pretty.new(2).write(document, output)
  "<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n#{output}\n"
end

def write_entitlements!(path, values)
  temporary = "#{path}.tmp-#{SecureRandom.hex(8)}"
  File.binwrite(temporary, entitlement_document(values))
  File.rename(temporary, path)
ensure
  FileUtils.rm_f(temporary) if temporary && File.exist?(temporary)
end

begin
  fail_tool("exactly one request file is required") unless ARGV.length == 1
  workspace = File.realpath(Dir.pwd)
  request_file = inside_workspace!(workspace, ARGV.fetch(0), "request file")
  fail_tool("request file does not exist") unless File.file?(request_file)
  request = validate_request!(JSON.parse(File.read(request_file)))

  project_relative = request["project_path"]
  project_path = inside_workspace!(workspace, File.join(workspace, project_relative), "project")
  fail_tool("project does not exist") unless File.directory?(project_path)

  require "xcodeproj"

  project_backup_root = Dir.mktmpdir(".xcodeproj-backup-", File.join(workspace, ".spacetime"))
  project_backup = File.join(project_backup_root, File.basename(project_path))
  FileUtils.cp_r(project_path, project_backup)
  entitlement_backups = {}

  begin
    project = Xcodeproj::Project.open(project_path)
    request["targets"].each do |target_request|
      target = project.targets.find { |candidate| candidate.name == target_request["name"] }
      fail_tool("target does not exist") unless target

      target_request["build_settings"].each do |setting|
        target.build_configurations.each { |configuration| configuration.build_settings[setting["key"]] = setting["value"] }
      end
      target_request["frameworks"].each { |item| add_link!(project, target, item) }
      target_request["libraries"].each { |item| add_link!(project, target, item) }

      unless target_request["entitlements"].empty?
        entitlement_name = "#{target.name}.entitlements"
        entitlement_path = File.join(File.dirname(project_path), entitlement_name)
        inside_workspace!(workspace, entitlement_path, "entitlements")
        entitlement_backups[entitlement_path] = File.file?(entitlement_path) ? File.binread(entitlement_path) : nil
        entitlement_values = target_request["entitlements"].to_h { |item| [item["key"], item["value"]] }
        write_entitlements!(entitlement_path, entitlement_values)
        target.build_configurations.each do |configuration|
          configuration.build_settings["CODE_SIGN_ENTITLEMENTS"] = entitlement_name
        end
        project.main_group.new_file(entitlement_name) unless project.main_group.files.any? { |file| file.path == entitlement_name }
      end
    end
    project.save
  rescue StandardError
    FileUtils.rm_rf(project_path)
    FileUtils.cp_r(project_backup, project_path)
    entitlement_backups.each do |path, content|
      if content.nil?
        FileUtils.rm_f(path)
      else
        File.binwrite(path, content)
      end
    end
    raise
  ensure
    FileUtils.rm_rf(project_backup_root)
  end

  STDOUT.write(JSON.generate({ "status" => "applied", "project_path" => project_relative }) + "\n")
rescue JSON::ParserError
  emit_failure("request JSON is invalid")
rescue SystemCallError, LoadError, StandardError => error
  emit_failure("xcode project tool failed: #{error.class}")
end
