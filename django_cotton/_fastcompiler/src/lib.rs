use std::collections::HashSet;

use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;

#[derive(Clone)]
struct Ignorable {
    placeholder: String,
    content: String,
}

struct ProcessedResult {
    compiled: String,
    dependencies: Vec<String>,
}

#[derive(Clone)]
struct Attribute {
    key: String,
    value: Option<String>,
}

#[derive(Clone)]
struct ParsedTag {
    original: String,
    name: String,
    attributes: Vec<Attribute>,
    attrs_repr: String,
    is_closing: bool,
    is_self_closing: bool,
    start: usize,
    end: usize,
}

#[pyfunction]
fn process(py: Python<'_>, html: &str) -> PyResult<String> {
    let html_owned = html.to_owned();
    let result = py.allow_threads(|| process_internal(&html_owned))?;
    Ok(result.compiled)
}

#[pyfunction]
fn process_with_dependencies(py: Python<'_>, html: &str) -> PyResult<(String, Vec<String>)> {
    let html_owned = html.to_owned();
    let result = py.allow_threads(|| process_internal(&html_owned))?;
    Ok((result.compiled, result.dependencies))
}

#[pyfunction]
fn get_dependencies(py: Python<'_>, html: &str) -> PyResult<Vec<String>> {
    let html_owned = html.to_owned();
    let result = py.allow_threads(|| process_internal(&html_owned))?;
    Ok(result.dependencies)
}

fn process_internal(html: &str) -> PyResult<ProcessedResult> {
    let (processed_html, ignorables) = exclude_ignorables(html);
    let (vars_content, processed_html) = process_c_vars(&processed_html)?;
    let (replacements, dependencies) = collect_replacements(&processed_html)?;

    let mut compiled = processed_html.clone();
    for (original, replacement) in replacements {
        compiled = compiled.replace(&original, &replacement);
    }

    if let Some(vars) = vars_content {
        compiled = format!("{}{}{{% endvars %}}", vars, compiled);
    }

    compiled = restore_ignorables(compiled, &ignorables);

    Ok(ProcessedResult { compiled, dependencies })
}

fn exclude_ignorables(html: &str) -> (String, Vec<Ignorable>) {
    let mut output = String::with_capacity(html.len());
    let mut ignorables = Vec::new();
    let mut index = 0;

    while index < html.len() {
        if let Some((end, content)) = match_ignorable(html, index) {
            let placeholder = format!("__COTTON_IGNORE_{}__", ignorables.len());
            output.push_str(&placeholder);
            ignorables.push(Ignorable { placeholder, content });
            index = end;
        } else if let Some(ch) = html[index..].chars().next() {
            output.push(ch);
            index += ch.len_utf8();
        } else {
            break;
        }
    }

    (output, ignorables)
}

fn match_ignorable(html: &str, start: usize) -> Option<(usize, String)> {
    let slice = &html[start..];

    if slice.starts_with("{%") {
        let after_brace = skip_whitespace(html, start + 2);

        if html[after_brace..].starts_with("cotton_verbatim") {
            let search_start = skip_identifier(html, after_brace + "cotton_verbatim".len());
            if let Some(end) = find_named_block_end(html, search_start, "endcotton_verbatim") {
                return Some((end, html[start..end].to_string()));
            }
        } else if html[after_brace..].starts_with("comment") {
            let search_start = skip_identifier(html, after_brace + "comment".len());
            if let Some(end) = find_named_block_end(html, search_start, "endcomment") {
                return Some((end, html[start..end].to_string()));
            }
        } else if let Some(end) = find_simple_close(html, start, "%}") {
            return Some((end, html[start..end].to_string()));
        }
    } else if slice.starts_with("{#") {
        if let Some(end) = find_simple_close(html, start, "#}") {
            return Some((end, html[start..end].to_string()));
        }
    } else if slice.starts_with("{{") {
        if let Some(end) = find_simple_close(html, start, "}}") {
            return Some((end, html[start..end].to_string()));
        }
    }

    None
}

fn find_named_block_end(html: &str, search_start: usize, closing_keyword: &str) -> Option<usize> {
    let mut pos = search_start;

    while pos < html.len() {
        let relative = html[pos..].find("{%")?;
        let block_start = pos + relative;
        let keyword_start = skip_whitespace(html, block_start + 2);

        if html[keyword_start..].starts_with(closing_keyword) {
            let after_keyword = skip_identifier(html, keyword_start + closing_keyword.len());
            let end_pos = skip_whitespace(html, after_keyword);
            if html[end_pos..].starts_with("%}") {
                return Some(end_pos + 2);
            }
        }

        pos = block_start + 2;
    }

    None
}

fn find_simple_close(html: &str, start: usize, needle: &str) -> Option<usize> {
    html[start..].find(needle).map(|offset| start + offset + needle.len())
}

fn skip_whitespace(html: &str, mut index: usize) -> usize {
    while index < html.len() {
        let ch = html.as_bytes()[index];
        if !ch.is_ascii_whitespace() {
            break;
        }
        index += 1;
    }
    index
}

fn skip_identifier(html: &str, mut index: usize) -> usize {
    while index < html.len() {
        let ch = html.as_bytes()[index];
        if ch.is_ascii_alphanumeric() || ch == b'_' {
            index += 1;
        } else {
            break;
        }
    }
    index
}

fn restore_ignorables(mut html: String, ignorables: &[Ignorable]) -> String {
    for ignorable in ignorables {
        let mut content = ignorable.content.clone();
        if content.trim_start().starts_with("{% cotton_verbatim") {
            content = extract_verbatim_inner(&content).to_string();
        }
        html = html.replace(&ignorable.placeholder, &content);
    }
    html
}

fn extract_verbatim_inner(content: &str) -> &str {
    if let Some(open_end) = content.find("%}") {
        let inner_start = open_end + 2;
        if let Some(close_start_rel) = content[inner_start..].find("{% endcotton_verbatim") {
            let close_start = inner_start + close_start_rel;
            return &content[inner_start..close_start];
        }
    }
    content
}

fn process_c_vars(html: &str) -> PyResult<(Option<String>, String)> {
    let mut vars_content: Option<String> = None;
    let mut output = String::with_capacity(html.len());
    let mut index = 0;

    while let Some(pos) = html[index..].find("<c-vars") {
        let absolute = index + pos;
        output.push_str(&html[index..absolute]);

        match parse_c_tag(html, absolute) {
            Ok(Some(tag)) => {
                if tag.name != "vars" {
                    output.push_str(&tag.original);
                    index = tag.end;
                    continue;
                }

                if tag.is_closing {
                    return Err(PyValueError::new_err(
                        "Unexpected closing c-vars tag without an opening tag.",
                    ));
                }

                if vars_content.is_some() {
                    return Err(PyValueError::new_err(
                        "Multiple c-vars tags found in component template. Only one c-vars tag is allowed per template.",
                    ));
                }

                let attrs_text = tag.attrs_repr.trim();
                vars_content = Some(format!("{{% vars {} %}}", attrs_text));

                if tag.is_self_closing {
                    index = tag.end;
                } else {
                    if let Some(close_rel) = html[tag.end..].find("</c-vars>") {
                        let close_end = tag.end + close_rel + "</c-vars>".len();
                        index = close_end;
                    } else {
                        return Err(PyValueError::new_err("Missing closing </c-vars> tag."));
                    }
                }
            }
            Ok(None) => {
                output.push_str(&html[absolute..absolute + 1]);
                index = absolute + 1;
            }
            Err(msg) => {
                return Err(build_py_error(&msg, html, absolute));
            }
        }
    }

    output.push_str(&html[index..]);

    Ok((vars_content, output))
}

fn collect_replacements(html: &str) -> PyResult<(Vec<(String, String)>, Vec<String>)> {
    let mut replacements = Vec::new();
    let mut dependencies = Vec::new();
    let mut seen_deps = HashSet::new();
    let mut index = 0;

    while index < html.len() {
        let Some(next_lt_rel) = html[index..].find('<') else {
            break;
        };
        let absolute = index + next_lt_rel;

        match parse_c_tag(html, absolute) {
            Ok(Some(tag)) => {
                index = tag.end;

                if tag.name.starts_with("__COTTON_IGNORE_") {
                    continue;
                }

                match tag.name.as_str() {
                    "vars" => {
                        continue;
                    }
                    "slot" => {
                        match process_slot(&tag) {
                            Ok(replacement) => {
                                replacements.push((tag.original.clone(), replacement));
                            }
                            Err(msg) => return Err(build_py_error(&msg, html, tag.start)),
                        }
                    }
                    _ => {
                        match process_component(&tag) {
                            Ok((replacement, dependency)) => {
                                replacements.push((tag.original.clone(), replacement));
                                if let Some(dep) = dependency {
                                    if seen_deps.insert(dep.clone()) {
                                        dependencies.push(dep);
                                    }
                                }
                            }
                            Err(msg) => return Err(build_py_error(&msg, html, tag.start)),
                        }
                    }
                }
            }
            Ok(None) => {
                index = absolute + 1;
            }
            Err(msg) => {
                return Err(build_py_error(&msg, html, absolute));
            }
        }
    }

    Ok((replacements, dependencies))
}

fn parse_c_tag(html: &str, start: usize) -> Result<Option<ParsedTag>, String> {
    if !html[start..].starts_with('<') {
        return Ok(None);
    }

    let mut idx = start + 1;
    if idx >= html.len() {
        return Ok(None);
    }

    let is_closing = html[idx..].starts_with('/');
    if is_closing {
        idx += 1;
    }

    if idx + 2 > html.len() || !html[idx..].starts_with("c-") {
        return Ok(None);
    }
    idx += 2;

    let name_start = idx;
    while idx < html.len() {
        let ch = html.as_bytes()[idx];
        if ch.is_ascii_whitespace() || ch == b'>' || ch == b'/' {
            break;
        }
        idx += 1;
    }

    if idx == name_start {
        return Err("c- tag missing component name".to_string());
    }

    let name = html[name_start..idx].to_string();
    let attr_start = idx;

    let mut pos = idx;
    let mut in_quote: Option<char> = None;
    while pos < html.len() {
        let ch = html.as_bytes()[pos] as char;
        if ch == '"' || ch == '\'' {
            if Some(ch) == in_quote {
                in_quote = None;
            } else if in_quote.is_none() {
                in_quote = Some(ch);
            }
        } else if ch == '>' && in_quote.is_none() {
            break;
        }
        pos += 1;
    }

    if pos >= html.len() {
        return Err("Unterminated c- tag".to_string());
    }

    let tag_end = pos + 1;

    if is_closing {
        return Ok(Some(ParsedTag {
            original: html[start..tag_end].to_string(),
            name,
            attributes: Vec::new(),
            attrs_repr: String::new(),
            is_closing: true,
            is_self_closing: false,
            start,
            end: tag_end,
        }));
    }

    let mut attr_end = pos;
    while attr_end > attr_start && html.as_bytes()[attr_end - 1].is_ascii_whitespace() {
        attr_end -= 1;
    }

    let mut is_self_closing = false;
    if attr_end > attr_start && html.as_bytes()[attr_end - 1] == b'/' {
        is_self_closing = true;
        attr_end -= 1;
        while attr_end > attr_start && html.as_bytes()[attr_end - 1].is_ascii_whitespace() {
            attr_end -= 1;
        }
    }

    let attrs_repr = html[attr_start..attr_end].to_string();
    let attributes = parse_attributes(&attrs_repr);

    Ok(Some(ParsedTag {
        original: html[start..tag_end].to_string(),
        name,
        attributes,
        attrs_repr,
        is_closing: false,
        is_self_closing,
        start,
        end: tag_end,
    }))
}

fn parse_attributes(attrs: &str) -> Vec<Attribute> {
    let mut attributes = Vec::new();
    let mut index = 0;

    while index < attrs.len() {
        index = skip_whitespace(attrs, index);
        if index >= attrs.len() {
            break;
        }

        let key_start = index;
        while index < attrs.len() {
            let ch = attrs.as_bytes()[index];
            if ch.is_ascii_whitespace() || ch == b'=' {
                break;
            }
            index += 1;
        }

        if key_start == index {
            break;
        }

        let key = attrs[key_start..index].to_string();
        index = skip_whitespace(attrs, index);

        if index < attrs.len() && attrs.as_bytes()[index] == b'=' {
            index += 1;
            index = skip_whitespace(attrs, index);
            if index >= attrs.len() {
                attributes.push(Attribute { key, value: None });
                break;
            }

            let ch = attrs.as_bytes()[index] as char;
            if ch == '"' || ch == '\'' {
                let quote = ch;
                index += 1;
                let value_start = index;
                while index < attrs.len() {
                    let current = attrs.as_bytes()[index] as char;
                    if current == quote {
                        break;
                    }
                    index += 1;
                }
                let value = attrs[value_start..index].to_string();
                if index < attrs.len() {
                    index += 1;
                }
                attributes.push(Attribute { key, value: Some(value) });
            } else {
                let value_start = index;
                while index < attrs.len() {
                    let current = attrs.as_bytes()[index];
                    if current.is_ascii_whitespace() {
                        break;
                    }
                    index += 1;
                }
                let value = attrs[value_start..index].to_string();
                attributes.push(Attribute { key, value: Some(value) });
            }
        } else {
            attributes.push(Attribute { key, value: None });
        }
    }

    attributes
}

fn process_slot(tag: &ParsedTag) -> Result<String, String> {
    if tag.is_closing {
        return Ok("{% endslot %}".to_string());
    }

    for attribute in &tag.attributes {
        if attribute.key == "name" {
            if let Some(value) = &attribute.value {
                return Ok(format!("{{% slot {} %}}", value));
            }
        }
    }

    Err(format!(
        "c-slot tag must have a name attribute: {}",
        tag.original
    ))
}

fn process_component(tag: &ParsedTag) -> Result<(String, Option<String>), String> {
    if tag.is_closing {
        return Ok(("{% endc %}".to_string(), None));
    }

    let (attrs_string, extracted) = build_attribute_strings(&tag.attributes);
    let opening_tag = format!("{{% c {}{} %}}", tag.name, attrs_string);
    let mut replacement = opening_tag;
    replacement.push_str(&extracted);
    if tag.is_self_closing {
        replacement.push_str("{% endc %}");
    }

    let dependency = if tag.name == "component" {
        match tag
            .attributes
            .iter()
            .find(|attribute| attribute.key == "is")
        {
            Some(attr) => {
                if let Some(value) = &attr.value {
                    if value.starts_with("__COTTON_IGNORE_") {
                        None
                    } else {
                        Some(value.clone())
                    }
                } else {
                    return Err("c-component tag must include an \"is\" attribute.".to_string());
                }
            }
            None => {
                return Err("c-component tag must include an \"is\" attribute.".to_string());
            }
        }
    } else if tag.name != "slot" && tag.name != "vars" && !tag.name.starts_with("__COTTON_IGNORE_") {
        Some(tag.name.clone())
    } else {
        None
    };

    Ok((replacement, dependency))
}

fn build_attribute_strings(attributes: &[Attribute]) -> (String, String) {
    let mut processed = Vec::new();
    let mut extracted = String::new();

    for attribute in attributes {
        match &attribute.value {
            None => processed.push(attribute.key.clone()),
            Some(value) => {
                if should_extract(value) {
                    extracted.push_str(&format!(
                        "{{% attr {} %}}{}{{% endattr %}}",
                        attribute.key, value
                    ));
                } else {
                    processed.push(format!(r#"{}="{}""#, attribute.key, value));
                }
            }
        }
    }

    let attrs_string = if processed.is_empty() {
        String::new()
    } else {
        format!(" {}", processed.join(" "))
    };

    (attrs_string, extracted)
}

fn should_extract(value: &str) -> bool {
    value.contains("{{")
        || value.contains("{%")
        || value.contains('=')
        || value.contains("__COTTON_IGNORE_")
}

fn build_py_error(message: &str, html: &str, position: usize) -> PyErr {
    let line = html[..position].chars().filter(|ch| *ch == '\n').count() + 1;
    PyValueError::new_err(format!("Error in template at line {}: {}", line, message))
}

#[pymodule]
fn _fastcompiler(_py: Python<'_>, m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(process, m)?)?;
    m.add_function(wrap_pyfunction!(process_with_dependencies, m)?)?;
    m.add_function(wrap_pyfunction!(get_dependencies, m)?)?;
    Ok(())
}
