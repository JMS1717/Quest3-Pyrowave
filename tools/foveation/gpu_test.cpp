// SPDX-License-Identifier: MIT
// CI-only software GLES check of the same inverse helper used by the Quest draw.
#include "../pyroclient/gpu_readback_gles.h"
#include <GLES3/gl3.h>
#include <algorithm>
#include <cmath>
#include <fstream>
#include <iostream>
#include <iterator>
#include <string>

static GLuint shader(GLenum kind, const std::string &text) {
    GLuint result = glCreateShader(kind);
    const char *data = text.c_str();
    glShaderSource(result, 1, &data, nullptr);
    glCompileShader(result);
    GLint success = 0;
    glGetShaderiv(result, GL_COMPILE_STATUS, &success);
    if (!success) {
        char log[4096] = {};
        glGetShaderInfoLog(result, sizeof(log), nullptr, log);
        std::cerr << log << '\n';
        glDeleteShader(result);
        return 0;
    }
    return result;
}

int main(int argc, char **argv) {
    if (argc != 3) return 2;
    std::ifstream source_file(argv[1]), fixture(argv[2]);
    if (!source_file || !fixture) return 2;
    std::string helper{std::istreambuf_iterator<char>(source_file), {}};
    q3pw::ReadbackContext context;
    std::string error;
    if (!context.initialize(error)) { std::cerr << error; return 1; }
    GLuint vertex = shader(GL_VERTEX_SHADER,
        "#version 300 es\nvoid main(){vec2 p=vec2(gl_VertexID&1,gl_VertexID>>1);gl_Position=vec4(p*2.0-1.0,0,1);}");
    GLuint fragment = shader(GL_FRAGMENT_SHADER,
        "#version 300 es\nprecision highp float;\n" + helper +
        "\nuniform vec2 test_uv;out vec4 color;void main(){color=vec4(light_foveated_uv(test_uv),0,1);}");
    if (!vertex || !fragment) return 1;
    GLuint program = glCreateProgram();
    glAttachShader(program, vertex); glAttachShader(program, fragment); glLinkProgram(program);
    GLint success = 0; glGetProgramiv(program, GL_LINK_STATUS, &success);
    if (!success) return 1;
    glUseProgram(program);
    GLfloat values[24];
    for (auto &v : values) if (!(fixture >> v)) return 2;
    for (unsigned i = 0; i < 3; i++) {
        const char *names[] = {"ffe_view_ratio", "ffe_edge_ratio", "ffe_c2"};
        glUniform2fv(glGetUniformLocation(program, names[i]), 1, values + i * 2);
    }
    glUniform4fv(glGetUniformLocation(program, "ffe_p[0]"), 4, values + 6);
    glUniform2fv(glGetUniformLocation(program, "ffe_half_texel"), 1, values + 22);
    GLuint target, fbo, array;
    glGenTextures(1, &target); glBindTexture(GL_TEXTURE_2D, target);
    glTexStorage2D(GL_TEXTURE_2D, 1, GL_RGBA32F, 1, 1);
    glGenFramebuffers(1, &fbo); glBindFramebuffer(GL_FRAMEBUFFER, fbo);
    glFramebufferTexture2D(GL_FRAMEBUFFER, GL_COLOR_ATTACHMENT0, GL_TEXTURE_2D, target, 0);
    if (glCheckFramebufferStatus(GL_FRAMEBUFFER) != GL_FRAMEBUFFER_COMPLETE) return 1;
    glGenVertexArrays(1, &array); glBindVertexArray(array); glViewport(0, 0, 1, 1);
    int eye; double u, v, expected_x, expected_y, worst = 0;
    unsigned cases = 0;
    while (fixture >> eye >> u >> v >> expected_x >> expected_y) {
        glUniform1i(glGetUniformLocation(program, "view_idx"), eye);
        glUniform2f(glGetUniformLocation(program, "test_uv"), GLfloat(u), GLfloat(v));
        glDrawArrays(GL_TRIANGLE_STRIP, 0, 4);
        GLfloat actual[4] = {};
        glReadPixels(0, 0, 1, 1, GL_RGBA, GL_FLOAT, actual);
        if (glGetError() != GL_NO_ERROR || !std::isfinite(actual[0]) || !std::isfinite(actual[1])) return 1;
        worst = std::max({worst, std::abs(actual[0] - expected_x), std::abs(actual[1] - expected_y)});
        if (worst > 0.000005 || actual[0] <= eye * .5 || actual[0] >= (eye + 1) * .5) {
            std::cerr << "Wrong mapping/eye at " << eye << ',' << u << ',' << v << " error=" << worst << '\n';
            return 1;
        }
        cases++;
    }
    glDeleteVertexArrays(1, &array); glDeleteFramebuffers(1, &fbo); glDeleteTextures(1, &target);
    glDeleteProgram(program); glDeleteShader(vertex); glDeleteShader(fragment);
    if (cases != 490) return 1;
    std::cout << "Light inverse GLES mapping: " << cases << " cases, worst UV error " << worst << '\n';
}
