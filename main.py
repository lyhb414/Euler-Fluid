import taichi as ti
import numpy as np
from PIL import Image

ti.init(arch=ti.cpu)

res = (512, 512)
pixels = ti.Vector.field(3, ti.f32, shape=res)
force_radius = 256
f_strength = 20000.0
decay = 1 - 1 / 120

grid = (512, 512)
Vx = ti.field(ti.f32, shape=grid)
Vy = ti.field(ti.f32, shape=grid)
Vx_buffer = ti.field(ti.f32, shape=grid)
Vy_buffer = ti.field(ti.f32, shape=grid)
V_divs = ti.field(ti.f32, shape=grid)

force = ti.Vector.field(2, ti.f32, shape=grid)
pressure = ti.field(ti.f32, shape=grid)

# tracer
n_imageTracer = 100000
imageTracer = ti.Vector.field(2, ti.f32, shape=n_imageTracer)
colorTracer = ti.field(ti.i32, shape=n_imageTracer)
indices = ti.field(ti.i32, shape=n_imageTracer)

imageColor = ti.field(ti.i32, shape=grid)
imageRed = ti.field(ti.u8, shape=grid)
imageGreen = ti.field(ti.u8, shape=grid)
imageBlue = ti.field(ti.u8, shape=grid)

image_path = 'Mona_Lisa.jpg'
img = Image.open(image_path).convert('RGB')
img = img.resize(grid)
img_array = np.array(img)

imageRed.from_numpy(img_array[:, :, 0])
imageGreen.from_numpy(img_array[:, :, 1])
imageBlue.from_numpy(img_array[:, :, 2])

last_curser = ti.Vector.field(2, ti.f32, ())
curser = ti.Vector.field(2, ti.f32, ())
picking = ti.field(ti.i32, ())
display = 0

# solver
@ti.kernel
def fill_laplacian_matrix(A: ti.types.sparse_matrix_builder()):
    for i, j in ti.ndrange(grid[0], grid[1]):
        row = i * grid[1] + j
        center = 0.0
        if j != 0:
            A[row, row - 1] += -1.0
            center += 1.0
        else:
            A[row, row + grid[1] - 1] += -1.0
            center += 1.0
        if j != grid[1] - 1:
            A[row, row + 1] += -1.0
            center += 1.0
        else:
            A[row, row - grid[1] + 1] += -1.0
            center += 1.0

        if i != 0:
            A[row, row - grid[1]] += -1.0
            center += 1.0
        else:
            A[row, grid[0] * grid[1] - grid[1] + j] += -1.0
            center += 1.0
        if i != grid[0] - 1:
            A[row, row + grid[1]] += -1.0
            center += 1.0
        else:
            A[row, j] += -1.0
            center += 1.0
        A[row, row] += center


N = grid[0] * grid[1]
K = ti.linalg.SparseMatrixBuilder(N, N, max_num_triplets=N * 6)
F_b = ti.ndarray(ti.f32, shape=N)

fill_laplacian_matrix(K)
L = K.build()
solver = ti.linalg.SparseSolver(solver_type="LLT")
solver.analyze_pattern(L)
solver.factorize(L)


@ti.kernel
def init():
    for i, j in ti.ndrange(grid[0], grid[1]):
        r = int(imageRed[i, j])
        g = int(imageGreen[i, j])
        b = int(imageBlue[i, j])
        color = (r << 16) | (g << 8) | b
        imageColor[j, 512 - i] = color

    for i in range(n_imageTracer):
        x = ti.random(dtype=ti.f32) * 512
        y = ti.random(dtype=ti.f32) * 512
        imageTracer[i] = [x ,y]
        colorTracer[i] = imageColor[ti.cast(ti.floor(x), ti.i32), ti.cast(ti.floor(y), ti.i32)]
        indices[i] = i


@ti.kernel
def clear_force():
    for i, j in ti.ndrange(grid[0], grid[1]):
        force[i, j] = ti.Vector([0, 0])


@ti.kernel
def curse_force(dt: ti.f32):
    d = (curser[None] - last_curser[None])
    dir = d / (d.norm() + 1e-5)

    for i, j in ti.ndrange(grid[0], grid[1]):
        dx, dy = (i + 0.5 - curser[None][0]), (j + 0.5 - curser[None][1])
        d2 = dx * dx + dy * dy
        factor = ti.exp(-d2 / force_radius)
        momentum = (dir * f_strength * factor) * dt
        Vx[i, j] += momentum[0]
        Vy[i, j] += momentum[1]

@ti.func
def add_force(df, i: ti.i32, j: ti.i32):
    force[i, j] += df


@ti.kernel
def apply_force(t: ti.f32):
    for i, j in ti.ndrange(grid[0], grid[1]):
        Vx[i, j] += force[i, j][0] * t
        Vy[i, j] += force[i, j][1] * t


@ti.kernel
def advect(t: ti.f32):
    for i, j in ti.ndrange(grid[0], grid[1]):
        semi_lagrangian(Vx, Vx_buffer, t, i, j)
        semi_lagrangian(Vy, Vy_buffer, t, i, j)
    for i, j in ti.ndrange(grid[0], grid[1]):
        Vx[i, j] = Vx_buffer[i, j] * decay
        Vy[i, j] = Vy_buffer[i, j] * decay


@ti.kernel
def diffuse(t: ti.f32):
    pass


@ti.kernel
def project(t: ti.f32):
    for i, j in ti.ndrange(grid[0], grid[1]):
        pl = retile_point([i - 1, j])
        pr = retile_point([i + 1, j])
        pb = retile_point([i, j - 1])
        pt = retile_point([i, j + 1])
        dV = 0.5 * ti.Vector([pressure[pr[0], pr[1]] -
                              pressure[pl[0], pl[1]],
                              pressure[pt[0], pt[1]] -
                              pressure[pb[0], pb[1]]])
        Vx[i, j] -= dV[0]
        Vy[i, j] -= dV[1]

@ti.kernel
def updateTracers(t: ti.f32):
    for i in range(n_imageTracer):
        x = t * sample_bilinear(Vx, imageTracer[i])
        y = t * sample_bilinear(Vy, imageTracer[i])
        imageTracer[i] = [imageTracer[i][0] + x, imageTracer[i][1] + y]
        imageTracer[i] = retile_point(imageTracer[i])


def update(t: ti.f32):
    apply_force(t)
    advect(t)
    diffuse(t)
    divergence()
    solve_pressure()
    project(t)

    clear_force()
    updateTracers(t)

@ti.kernel
def divergence():
    for i, j in ti.ndrange(grid[0], grid[1]):
        vl = retile_point([i - 1, j])
        vr = retile_point([i + 1, j])
        vb = retile_point([i, j - 1])
        vt = retile_point([i, j + 1])
        V_divs[i, j] = (Vx[vr[0], vr[1]] -
                        Vx[vl[0], vl[1]] +
                        Vy[vt[0], vt[1]] -
                        Vy[vb[0], vb[1]]) * 0.5 * 2
@ti.func
def backtrace(i, j, dt):
    # RK1
    x_now = ti.Vector([i + 0.5, j + 0.5])
    V = ti.Vector([Vx[i, j], Vy[i, j]])
    x_pre = x_now - V * dt
    return x_pre


@ti.func
def semi_lagrangian(x, new_x, dt, i, j):
    #new_x[i, j] = sample_bilinear(x, retile_point(backtrace(i, j, dt)))
    point = retile_point(backtrace(i, j, dt))
    #new_x[i, j] = x[ti.cast(point[0], ti.i32), ti.cast(point[1], ti.i32)]
    new_x[i, j] = sample_bilinear(x, point)


@ti.func
def pos2Index(p):
    return [ti.cast(p[0], ti.i32), ti.cast(p[1], ti.i32)]


@ti.func
def retile_point(p):
    if p[0] >= 512:
        p[0] -= 512
    if p[1] >= 512:
        p[1] -= 512
    if p[0] < 0:
        p[0] += 512
    if p[1] < 0:
        p[1] += 512
    return p


@ti.func
def lerp(vl, vr, frac):
    # frac: [0.0, 1.0]
    return vl + frac * (vr - vl)

@ti.func
def sample_bilinear(x, p):
    u, v = p
    s, t = u - 0.5, v - 0.5
    # floor
    iu, iv = ti.cast(ti.floor(s), ti.i32), ti.cast(ti.floor(t), ti.i32)
    # fract
    fu, fv = s - iu, t - iv
    a = x[retile_point([iu, iv])]
    b = x[retile_point([iu + 1, iv])]
    c = x[retile_point([iu, iv + 1])]
    d = x[retile_point([iu + 1, iv + 1])]
    return lerp(lerp(a, b, fu), lerp(c, d, fu), fv)


@ti.kernel
def copy_divergence(div_in: ti.template(), div_out: ti.types.ndarray()):
    for I in ti.grouped(div_in):
        div_out[I[0] * grid[1] + I[1]] = -div_in[I]
@ti.kernel
def apply_pressure(p_in: ti.types.ndarray(), p_out: ti.template()):
    for I in ti.grouped(p_out):
        p_out[I] = p_in[I[0] * grid[1] + I[1]]

def solve_pressure():
    copy_divergence(V_divs, F_b)
    x = solver.solve(F_b)
    apply_pressure(x, pressure)

@ti.kernel
def render():
    for i, j in pixels:
        pixels[i, j] = ti.Vector([Vx[i, j] * 0.01 + 0.5, Vy[i, j] * 0.01 + 0.5, 0.5])

if __name__ == "__main__":
    init()
    gui = ti.GUI("Euler fluid", res, background_color=0x0)
    dt = 0.016
    while gui.running:
        for e in gui.get_events(ti.GUI.PRESS):
            if e.key in [ti.GUI.ESCAPE, ti.GUI.EXIT]:
                exit()
            if e.key == '1':
                if display == 0:
                    display = 1
                else:
                    display = 0

        if gui.is_pressed(ti.GUI.LMB):
            curser[None][0] = gui.get_cursor_pos()[0] * res[0]
            curser[None][1] = gui.get_cursor_pos()[1] * res[1]
            if last_curser[None][0] < 0:
                last_curser[None][0] = curser[None][0]
                last_curser[None][1] = curser[None][1]
            else:
                curse_force(dt)
                last_curser[None][0] = curser[None][0]
                last_curser[None][1] = curser[None][1]
        else:
            last_curser[None][0] = -1

        for r in range(2):
            update(dt)
        if display == 0:
            gui.circles(imageTracer.to_numpy() / 512, radius=1, palette=colorTracer.to_numpy(), palette_indices=indices.to_numpy())
        else:
            render()
            gui.set_image(pixels)
        gui.show()
